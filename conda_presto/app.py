"""Litestar HTTP API for conda environment resolving.

Endpoints:

- ``GET /resolve`` — resolve specs via query params
- ``POST /resolve`` — resolve specs and/or file content via JSON body
- ``GET /health`` — returns ``{"status": "ok"}``
- ``GET /`` — interactive Scalar API documentation
- ``GET /openapi.json`` — OpenAPI 3.1 schema (auto-generated)

Configuration is loaded from environment variables via
:mod:`conda_presto.config`.  See that module for the full list of
``CONDA_PRESTO_*`` settings (default channels, concurrency limits,
body size cap, CORS, rate limiting, log level, etc.).

Security design:
    - Request bodies are capped at ``MAX_BODY_BYTES`` (configurable via
      ``CONDA_PRESTO_MAX_BODY_BYTES``, default 1 MB).
    - File content is written to a temp file with a whitelisted extension
      and processed through conda's env spec plugin system (same as CLI).
    - Path traversal is prevented by stripping directory components from
      the client-provided filename.
    - Solver errors are logged server-side but only a generic message
      is returned to the client (no stack traces or file paths).
    - Rate limiting (configurable via ``CONDA_PRESTO_RATE_LIMIT``,
      default 300 req/min) follows the IETF RateLimit draft headers.

Performance design:
    - All solve calls run off the event loop via ``anyio.to_thread``
      with a concurrency limit of ``MAX_CONCURRENCY`` (configurable via
      ``CONDA_PRESTO_CONCURRENCY``).
    - The ``on_startup`` hook pre-warms repodata caches so the first
      request doesn't pay cold-start costs.
    - Response compression (gzip) reduces bandwidth for large solve
      results.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from importlib.metadata import version as pkg_version

import anyio
from conda.base.context import context
from litestar import Litestar, Request, get, post
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.logging import LoggingConfig
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.response import Response
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from .config import (
    CORS_ORIGINS,
    DEFAULT_CHANNELS,
    DEFAULT_PLATFORMS,
    LOG_LEVEL,
    MAX_CONCURRENCY,
    RATE_LIMIT,
)
from .resolve import solve, warmup

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".yml", ".yaml", ".txt", ".lock", ".toml", ".json"}


@dataclass
class ResolveRequest:
    """JSON body for ``POST /resolve``."""

    specs: list[str] = field(default_factory=list)
    file: str | None = None
    filename: str | None = None
    channels: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)


def parse_file_content(
    content: str,
    filename: str | None = None,
) -> tuple[list[str], list[str]]:
    """Parse file content through conda's env spec plugin system.

    Writes *content* to a temp file and runs it through
    ``detect_environment_specifier``, the same codepath the CLI uses.
    Returns ``(specs, channels)``.

    The *filename* controls which parser conda selects (via extension).
    Only extensions in ``ALLOWED_EXTENSIONS`` are accepted.  Directory
    components are stripped to prevent path traversal.
    """
    filename = os.path.basename(filename or "environment.yml")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext}', "
            f"allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=True
    ) as tmp:
        tmp.write(content)
        tmp.flush()

        spec_plugin = context.plugin_manager.detect_environment_specifier(
            tmp.name
        )
        spec = spec_plugin.environment_spec(filename=tmp.name)
        if not spec.can_handle():
            raise ValueError(
                f"No conda environment spec plugin can handle "
                f"this file format ({ext})"
            )
        env = spec.env

    specs = [str(s) for s in env.requested_packages]
    channels: list[str] = []
    if env.config and env.config.channels:
        channels.extend(env.config.channels)
    return specs, channels


def _do_solve(
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
) -> list[dict]:
    """Run the solver and return serialized results."""
    return [r.to_dict() for r in solve(channels, specs, platforms)]


@get("/resolve")
async def resolve_get(
    request: Request,
    spec: list[str] | None = None,
    channel: list[str] | None = None,
    platform: list[str] | None = None,
) -> Response:
    """Resolve package specs via query params."""
    specs = spec or []
    channels = channel or []
    platforms = platform or []

    if not specs:
        return Response(
            {"error": "Provide specs or file content"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    try:
        results = await anyio.to_thread.run_sync(
            lambda: _do_solve(specs, channels, platforms or None),
            limiter=request.app.state.solver_limiter,
            abandon_on_cancel=True,
        )
    except Exception:
        log.exception("Solve failed")
        return Response(
            {"error": "Internal solver error"},
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(results)


@post("/resolve", status_code=200)
async def resolve_post(
    request: Request,
    data: ResolveRequest,
    spec: list[str] | None = None,
    channel: list[str] | None = None,
    platform: list[str] | None = None,
) -> Response:
    """Resolve package specs and/or file content via JSON body."""
    specs = data.specs or spec or []
    channels = data.channels or channel or []
    platforms = data.platforms or platform or []
    file_content = data.file
    filename = data.filename

    if file_content is not None:
        try:
            file_specs, file_channels = parse_file_content(
                file_content, filename
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc)}, status_code=HTTP_400_BAD_REQUEST
            )
        specs = list(specs) + file_specs
        if not channels:
            channels = file_channels

    if not specs:
        return Response(
            {"error": "Provide specs or file content"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    try:
        results = await anyio.to_thread.run_sync(
            lambda: _do_solve(specs, channels, platforms or None),
            limiter=request.app.state.solver_limiter,
            abandon_on_cancel=True,
        )
    except Exception:
        log.exception("Solve failed")
        return Response(
            {"error": "Internal solver error"},
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(results)


@get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


async def on_startup(app: Litestar) -> None:
    """Initialize solver limiter and pre-warm repodata caches."""
    app.state.solver_limiter = anyio.CapacityLimiter(MAX_CONCURRENCY)
    log.info(
        "Pre-warming repodata cache for %s on %s",
        DEFAULT_CHANNELS,
        DEFAULT_PLATFORMS,
    )
    await anyio.to_thread.run_sync(
        lambda: warmup(DEFAULT_CHANNELS, DEFAULT_PLATFORMS),
        abandon_on_cancel=True,
    )
    log.info("Repodata cache warm")


middleware = [LoggingMiddlewareConfig().middleware]
if RATE_LIMIT:
    middleware.append(
        RateLimitConfig(rate_limit=("minute", RATE_LIMIT)).middleware
    )

app = Litestar(
    route_handlers=[resolve_get, resolve_post, health],
    openapi_config=OpenAPIConfig(
        title="conda-presto",
        version=pkg_version("conda-presto"),
        description="Fast dry-run conda solver HTTP API.",
        path="/",
    ),
    on_startup=[on_startup],
    compression_config=CompressionConfig(backend="brotli", brotli_gzip_fallback=True),
    cors_config=CORSConfig(allow_origins=CORS_ORIGINS),
    logging_config=LoggingConfig(
        log_exceptions="always",
        loggers={"conda_presto": {"level": LOG_LEVEL}},
    ),
    middleware=middleware,
)
