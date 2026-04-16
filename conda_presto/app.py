"""Starlette HTTP API for conda environment resolving.

Endpoints:

- ``GET /resolve`` — resolve specs via query params
- ``POST /resolve`` — resolve specs and/or file content via JSON body
- ``GET /health`` — returns ``{"status": "ok"}``
- ``GET /openapi.json`` — OpenAPI 3.1 schema

Configuration is loaded from environment variables via
:mod:`conda_presto.config`.  See that module for the full list of
``CONDA_PRESTO_*`` settings (default channels, concurrency limits,
body size cap, etc.).

Security design:
    - Request bodies are capped at ``MAX_BODY_BYTES`` (configurable via
      ``CONDA_PRESTO_MAX_BODY_BYTES``, default 1 MB).
    - File content is written to a temp file with a whitelisted extension
      and processed through conda's env spec plugin system (same as CLI).
    - Path traversal is prevented by stripping directory components from
      the client-provided filename.
    - Solver errors are logged server-side but only a generic message
      is returned to the client (no stack traces or file paths).

Performance design:
    - All solve calls run off the event loop via ``anyio.to_thread``
      with a concurrency limit of ``MAX_CONCURRENCY`` (configurable via
      ``CONDA_PRESTO_CONCURRENCY``).
    - The lifespan handler pre-warms repodata caches (also off the
      event loop) so the first request doesn't pay cold-start costs.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from importlib.metadata import version as pkg_version

import anyio
from conda.base.context import context
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import DEFAULT_CHANNELS, DEFAULT_PLATFORMS, MAX_BODY_BYTES, MAX_CONCURRENCY
from .resolve import solve, warmup

solver_limiter: anyio.CapacityLimiter | None = None

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".yml", ".yaml", ".txt", ".lock", ".toml", ".json"}


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


async def resolve(request: Request) -> JSONResponse:
    """``GET|POST /resolve`` — resolve package specs to pinned packages.

    GET uses query params (``spec``, ``channel``, ``platform``).
    POST accepts a JSON body with ``specs``, ``file``, ``filename``,
    ``channels``, and ``platforms``.  Body fields override query params.
    """
    specs = request.query_params.getlist("spec")
    channels = request.query_params.getlist("channel")
    platforms = request.query_params.getlist("platform")
    file_content = None
    filename = None

    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    return JSONResponse(
                        {"error": "Request body too large"}, status_code=413
                    )
            except (ValueError, OverflowError):
                return JSONResponse(
                    {"error": "Invalid Content-Length header"},
                    status_code=413,
                )
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse(
                {"error": "Request body too large"}, status_code=413
            )

        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return JSONResponse(
                {"error": f"Invalid JSON: {exc}"}, status_code=400
            )

        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "Expected a JSON object"}, status_code=400
            )

        for field in ("specs", "channels", "platforms"):
            val = body.get(field)
            if val is not None:
                if not isinstance(val, list) or not all(
                    isinstance(v, str) for v in val
                ):
                    return JSONResponse(
                        {"error": f"'{field}' must be a list of strings"},
                        status_code=400,
                    )

        specs = body.get("specs", specs)
        channels = body.get("channels", channels)
        platforms = body.get("platforms", platforms)
        file_content = body.get("file")
        filename = body.get("filename")

        if file_content is not None and not isinstance(file_content, str):
            return JSONResponse(
                {"error": "'file' must be a string"}, status_code=400
            )

    if file_content is not None:
        try:
            file_specs, file_channels = parse_file_content(
                file_content, filename
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        specs = list(specs) + file_specs
        if not channels:
            channels = file_channels

    if not specs:
        return JSONResponse(
            {"error": "Provide specs or file content"}, status_code=400
        )

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    try:
        results = await anyio.to_thread.run_sync(
            lambda: [
                r.to_dict()
                for r in solve(channels, specs, platforms or None)
            ],
            limiter=solver_limiter,
            abandon_on_cancel=True,
        )
    except Exception:
        log.exception("Solve failed")
        return JSONResponse(
            {"error": "Internal solver error"}, status_code=500
        )
    return JSONResponse(results)


async def health(request: Request) -> JSONResponse:
    """``GET /health`` — liveness probe."""
    return JSONResponse({"status": "ok"})


OPENAPI_SCHEMA = {
    "openapi": "3.1.0",
    "info": {
        "title": "conda-presto",
        "version": pkg_version("conda-presto"),
        "description": "Fast dry-run conda solver HTTP API.",
    },
    "paths": {
        "/resolve": {
            "get": {
                "summary": "Resolve package specs (query params)",
                "parameters": [
                    {
                        "name": "spec",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Package spec (repeatable)",
                    },
                    {
                        "name": "channel",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Channel (repeatable)",
                    },
                    {
                        "name": "platform",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Target platform (repeatable)",
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Resolved packages per platform",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {
                                        "$ref": "#/components/schemas/SolveResult"
                                    },
                                }
                            }
                        },
                    }
                },
            },
            "post": {
                "summary": "Resolve package specs and/or file content",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/ResolveRequest"
                            }
                        }
                    },
                },
                "parameters": [
                    {
                        "name": "spec",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Package spec (repeatable, overridden by body)",
                    },
                    {
                        "name": "channel",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Channel (repeatable, overridden by body)",
                    },
                    {
                        "name": "platform",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Target platform "
                        "(repeatable, overridden by body)",
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Resolved packages per platform",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {
                                        "$ref": "#/components/schemas/SolveResult"
                                    },
                                }
                            }
                        },
                    }
                },
            },
        },
        "/health": {
            "get": {
                "summary": "Liveness probe",
                "responses": {
                    "200": {
                        "description": "Server is healthy",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string"}
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    },
    "components": {
        "schemas": {
            "ResolveRequest": {
                "type": "object",
                "properties": {
                    "specs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Package specs (e.g. python=3.13)",
                    },
                    "file": {
                        "type": "string",
                        "description": "Environment file content",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Filename hint for "
                        "format detection",
                    },
                    "channels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Conda channels",
                    },
                    "platforms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Target platforms (e.g. linux-64)",
                    },
                },
            },
            "SolveResult": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                    "packages": {
                        "type": "array",
                        "items": {
                            "$ref": "#/components/schemas/ResolvedPackage"
                        },
                    },
                    "error": {
                        "type": "string",
                        "nullable": True,
                    },
                },
            },
            "ResolvedPackage": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "build": {"type": "string"},
                    "build_number": {"type": "integer"},
                    "channel": {"type": "string"},
                    "subdir": {"type": "string"},
                    "url": {"type": "string"},
                    "sha256": {"type": "string"},
                    "md5": {"type": "string"},
                    "size": {"type": "integer"},
                    "depends": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "constrains": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


async def openapi_schema(request: Request) -> JSONResponse:
    """``GET /openapi.json`` — serve the OpenAPI schema."""
    return JSONResponse(OPENAPI_SCHEMA)


@asynccontextmanager
async def lifespan(app: Starlette):
    """Pre-warm repodata caches on startup."""
    global solver_limiter
    solver_limiter = anyio.CapacityLimiter(MAX_CONCURRENCY)
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
    yield


app = Starlette(
    routes=[
        Route("/resolve", resolve, methods=["GET", "POST"]),
        Route("/health", health, methods=["GET"]),
        Route("/openapi.json", openapi_schema, methods=["GET"]),
    ],
    lifespan=lifespan,
)
