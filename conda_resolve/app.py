"""Starlette HTTP API for conda environment resolving.

Endpoints:

- ``POST /solve`` — accepts JSON with channels, dependencies, platforms
- ``POST /solve/environment-yml`` — accepts raw YAML body
- ``GET /health`` — returns ``{"status": "ok"}``
- ``POST /cache/clear`` — drops all cached repodata indexes

Security design:
    - All request bodies are capped at 1 MB to prevent memory exhaustion.
    - JSON input is validated for correct types before processing.
    - Solver errors are logged server-side but only a generic message
      is returned to the client (no stack traces or file paths).

Performance design:
    - All solve calls run off the event loop via ``anyio.to_thread``
      so the server stays responsive during long solves.
    - The lifespan handler pre-warms repodata caches (also off the
      event loop) so the first request doesn't pay cold-start costs.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import anyio
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .resolve import clear_index_cache, index_cache, solve, warmup

solver_limiter: anyio.CapacityLimiter | None = None

log = logging.getLogger(__name__)

WARMUP_CHANNELS = ["conda-forge"]
WARMUP_PLATFORMS = ["linux-64", "osx-arm64", "osx-64"]

# 1 MB — generous for any environment.yml or JSON spec payload;
# prevents a malicious client from exhausting server memory.
MAX_BODY_BYTES = 1_024 * 1_024


async def _read_body(
    request: Request, limit: int = MAX_BODY_BYTES
) -> bytes:
    """Read the request body, rejecting payloads over *limit* bytes.

    Checks both the ``Content-Length`` header (fast reject before
    reading) and the actual body length (defense against missing or
    spoofed headers).
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                raise ValueError("Request body too large")
        except (ValueError, OverflowError):
            raise ValueError("Invalid Content-Length header")
    body = await request.body()
    if len(body) > limit:
        raise ValueError("Request body too large")
    return body


def _validate_solve_body(
    body: dict,
) -> tuple[list[str], list[str], list[str]]:
    """Extract and validate channels/dependencies/platforms from JSON.

    Rejects non-string values to prevent type confusion errors
    deep in the solver.  Returns ``(channels, dependencies, platforms)``.
    """
    channels = body.get("channels", ["defaults"])
    dependencies = body.get("dependencies", [])
    platforms = body.get("platforms", [])

    if not isinstance(channels, list) or not all(
        isinstance(c, str) for c in channels
    ):
        raise ValueError("'channels' must be a list of strings")
    if not isinstance(dependencies, list) or not all(
        isinstance(d, str) for d in dependencies
    ):
        raise ValueError("'dependencies' must be a list of strings")
    if not isinstance(platforms, list) or not all(
        isinstance(p, str) for p in platforms
    ):
        raise ValueError("'platforms' must be a list of strings")

    return channels, dependencies, platforms


def _parse_environment_yml(
    source: str | bytes,
    *,
    platforms: list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Parse an environment.yml into ``(channels, dependencies, platforms)``.

    Only conda dependencies (plain strings) are extracted; pip
    sub-dicts are silently skipped.  Uses ``yaml.safe_load`` to
    prevent arbitrary code execution from untrusted input.
    """
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Expected a YAML mapping")

    deps = data.get("dependencies", [])
    conda_deps = [d for d in deps if isinstance(d, str)]
    raw_channels = data.get("channels", ["defaults"])
    if not isinstance(raw_channels, list) or not all(
        isinstance(c, str) for c in raw_channels
    ):
        raise ValueError("'channels' must be a list of strings")

    return raw_channels, conda_deps, platforms or []


def _solve_and_serialize(
    channels: list[str],
    dependencies: list[str],
    platforms: list[str] | None,
) -> list[dict]:
    """Run solve and serialize results in one shot.

    Both the solve and the to_dict() serialization are CPU work that
    should stay off the event loop.
    """
    results = solve(channels, dependencies, platforms)
    return [r.to_dict() for r in results]


async def _run_solve(
    channels: list[str],
    dependencies: list[str],
    platforms: list[str] | None,
) -> list[dict]:
    """Run solve off the event loop so other requests aren't blocked.

    Without this, a multi-second solve would starve all concurrent
    connections including health checks.
    """
    return await anyio.to_thread.run_sync(
        lambda: _solve_and_serialize(channels, dependencies, platforms),
        limiter=solver_limiter,
        abandon_on_cancel=True,
    )


async def solve_specs(request: Request) -> JSONResponse:
    """``POST /solve`` — resolve package specs to pinned packages."""
    try:
        raw = await _read_body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)

    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return JSONResponse(
            {"error": f"Invalid JSON: {exc}"}, status_code=400
        )

    try:
        channels, dependencies, platforms = _validate_solve_body(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    try:
        output = await _run_solve(channels, dependencies, platforms or None)
    except Exception:
        log.exception("Solve failed")
        return JSONResponse(
            {"error": "Internal solver error"}, status_code=500
        )
    return JSONResponse(output)


async def solve_environment_yml(request: Request) -> JSONResponse:
    """``POST /solve/environment-yml`` — resolve from YAML body."""
    try:
        body = await _read_body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)

    query_platforms = request.query_params.getlist("platform")
    try:
        channels, dependencies, platforms = _parse_environment_yml(
            body, platforms=query_platforms or None
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        output = await _run_solve(
            channels, dependencies, platforms or None
        )
    except Exception:
        log.exception("Solve failed")
        return JSONResponse(
            {"error": "Internal solver error"}, status_code=500
        )
    return JSONResponse(output)


async def health(request: Request) -> JSONResponse:
    """``GET /health`` — liveness probe."""
    return JSONResponse({"status": "ok"})


async def cache_clear(request: Request) -> JSONResponse:
    """``POST /cache/clear`` — drop all cached repodata indexes."""
    count = len(index_cache)
    clear_index_cache()
    return JSONResponse({"cleared": count})


@asynccontextmanager
async def lifespan(app: Starlette):
    """Pre-warm repodata caches on startup.

    Runs in a thread to avoid blocking the event loop during the
    potentially slow repodata fetch.
    """
    global solver_limiter
    solver_limiter = anyio.CapacityLimiter(4)
    log.info(
        "Pre-warming repodata cache for %s on %s",
        WARMUP_CHANNELS,
        WARMUP_PLATFORMS,
    )
    await anyio.to_thread.run_sync(
        lambda: warmup(WARMUP_CHANNELS, WARMUP_PLATFORMS),
        abandon_on_cancel=True,
    )
    log.info("Repodata cache warm")
    yield


app = Starlette(
    routes=[
        Route("/solve", solve_specs, methods=["POST"]),
        Route(
            "/solve/environment-yml",
            solve_environment_yml,
            methods=["POST"],
        ),
        Route("/health", health, methods=["GET"]),
        Route("/cache/clear", cache_clear, methods=["POST"]),
    ],
    lifespan=lifespan,
)
