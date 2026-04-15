"""Starlette application for conda environment resolving."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .resolve import SolveRequest, solve, warmup

log = logging.getLogger(__name__)

WARMUP_CHANNELS = ["conda-forge"]
WARMUP_PLATFORMS = ["linux-64", "osx-arm64", "osx-64"]

MAX_BODY_BYTES = 1_024 * 1_024


async def _read_body(request: Request, limit: int = MAX_BODY_BYTES) -> bytes:
    """Read the request body, rejecting payloads over *limit* bytes."""
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > limit:
        raise ValueError("Request body too large")
    body = await request.body()
    if len(body) > limit:
        raise ValueError("Request body too large")
    return body


def _validate_solve_body(body: dict) -> SolveRequest:
    """Build a SolveRequest from a JSON dict, validating types."""
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

    return SolveRequest(
        channels=channels,
        dependencies=dependencies,
        platforms=platforms,
    )


async def _run_solve(req: SolveRequest) -> list[dict]:
    """Run solve off the event loop so other requests aren't blocked."""
    results = await anyio.to_thread.run_sync(lambda: solve(req))
    return [r.to_dict() for r in results]


async def solve_specs(request: Request) -> JSONResponse:
    """Resolve package specs to fully pinned packages with SHA256 hashes."""
    try:
        raw = await _read_body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)

    try:
        import json

        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return JSONResponse(
            {"error": f"Invalid JSON: {exc}"}, status_code=400
        )

    try:
        req = _validate_solve_body(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    try:
        output = await _run_solve(req)
    except Exception:
        log.exception("Solve failed")
        return JSONResponse(
            {"error": "Internal solver error"}, status_code=500
        )
    return JSONResponse(output)


async def solve_environment_yml(request: Request) -> JSONResponse:
    """Accept an environment.yml as the request body and resolve packages."""
    try:
        body = await _read_body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)

    platforms = request.query_params.getlist("platform")
    try:
        req = SolveRequest.from_environment_yml(
            body, platforms=platforms or None
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        output = await _run_solve(req)
    except Exception:
        log.exception("Solve failed")
        return JSONResponse(
            {"error": "Internal solver error"}, status_code=500
        )
    return JSONResponse(output)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@asynccontextmanager
async def lifespan(app: Starlette):
    log.info(
        "Pre-warming repodata cache for %s on %s",
        WARMUP_CHANNELS,
        WARMUP_PLATFORMS,
    )
    await anyio.to_thread.run_sync(
        lambda: warmup(WARMUP_CHANNELS, WARMUP_PLATFORMS)
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
    ],
    lifespan=lifespan,
)
