# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-04-16

### Added

- Separate Docker image flavors: server (`latest`) and CLI (`cli`),
  built from `docker/server.Dockerfile` and `docker/cli.Dockerfile`.
  The CLI image excludes server dependencies (starlette, uvicorn) for
  a smaller footprint.

## [0.2.0] - 2026-04-16

### Added

- Configuration module (`config.py`) with environment variable overrides
  for all operational settings: `CONDA_RESOLVE_CHANNELS`,
  `CONDA_RESOLVE_PLATFORMS`, `CONDA_RESOLVE_CONCURRENCY`,
  `CONDA_RESOLVE_WORKERS`, `CONDA_RESOLVE_MAX_BODY_BYTES`,
  `CONDA_RESOLVE_HOST`, `CONDA_RESOLVE_PORT`,
  `CONDA_RESOLVE_GLIBC_VERSION`, `CONDA_RESOLVE_LINUX_VERSION`,
  `CONDA_RESOLVE_OSX_VERSION`.
- Support for `.toml` and `.json` file extensions in the HTTP API
  file upload endpoint.
- Dynamic OpenAPI schema version derived from package metadata
  via `importlib.metadata`.

### Changed

- Default fallback channel changed from `defaults` to `conda-forge`
  in both CLI and HTTP API, configurable via `CONDA_RESOLVE_CHANNELS`.
- Unified HTTP API to single `/resolve` endpoint supporting both
  GET (query params) and POST (JSON body). Removed old `/solve`,
  `/solve/environment-yml`, and `/cache/clear` endpoints.
- Request body uses `specs` field instead of `dependencies`, and
  `file`/`filename` fields for environment file content instead of
  raw YAML body.
- POST body fields override query params when both are present.
- Virtual package versions for cross-platform solving are now
  configurable via environment variables instead of hardcoded.
- Process pool size and server host/port defaults are now
  configurable via environment variables.
- Updated test suite to match the new `/resolve` API.

## [0.1.1] - 2026-04-15

### Added

- Minimal, hardened Docker image using multi-stage build with
  `debian:bookworm-slim` and non-root user.
- Docker release workflow publishing multi-arch images (linux/amd64,
  linux/arm64) to GitHub Container Registry on release and manual
  dispatch.
- Dependabot configuration for Docker base image updates.
- Automated pixi lockfile update workflow (monthly, via
  `pixi-diff-to-markdown`).
- `linux-aarch64` platform support.

### Fixed

- Default platform detection after server warmup. The warmup loop
  left a stale platform in `context._cache_`, causing the server to
  default to the wrong platform. Now captured once at import time
  as `NATIVE_SUBDIR`.

## [0.1.0] - 2026-04-15

### Added

- Core solver module (`resolve.py`) with `ResolvedPackage` and
  `SolveResult` dataclasses using `slots=True` for memory efficiency.
- Split architecture: CLI uses conda's `Environment` model directly
  via `solve_environments()`, server uses lightweight custom types
  via `solve()` for fast JSON serialization.
- Shared `dispatch()` helper for single- and multi-platform solving,
  with per-platform error handling for the server path.
- Multi-platform parallel solving via `ProcessPoolExecutor` with
  persistent worker pool for cross-request cache reuse.
- Cross-platform virtual package injection (`configure_platform()`)
  using `context._cache_` for thread-safe platform configuration
  with conservative defaults (glibc 2.17, linux 5.15, osx 11.0).
- Thread-safe solver invocation via `platform_lock` to prevent
  concurrent requests from racing on process-global state.
- In-memory repodata index cache with `threading.Lock`-guarded
  check-then-build for thundering herd protection, and explicit
  `clear_index_cache()` invalidation.
- HTTP API (`app.py`) built on Starlette with `/solve`,
  `/solve/environment-yml`, `/health`, and `/cache/clear` endpoints.
- `anyio.CapacityLimiter` to cap concurrent solver threads and
  `abandon_on_cancel=True` for client disconnect handling.
- Request body size limit (1 MB), input type validation (both JSON
  and YAML endpoints), Content-Length header validation, and
  sanitized error responses (no internal stack traces leaked).
- Repodata cache pre-warming on server startup via `warmup()`,
  offloaded from the event loop with `anyio.to_thread.run_sync`.
- CLI with resolve as the default action and `--serve` flag for the
  HTTP server. Uses conda's environment specifier plugins for input
  and exporter plugins for output.
- Custom `resolve-json` environment exporter providing full package
  metadata (sha256, md5, urls, sizes, dependencies) as the default
  CLI output format.
- Conda subcommand plugin (`conda resolve`) with lazy imports
  to keep plugin load under 1 ms.
- Lazy uvicorn import in `cmd_serve()` to reduce CLI startup
  overhead by ~100 ms for non-server invocations.
- Performance tuning via pixi activation env: `CONDA_SOLVER=rattler`,
  `CONDA_CHANNEL_PRIORITY=strict`,
  `CONDA_NO_LOCK=true`, `CONDA_UNSATISFIABLE_HINTS=false`,
  `CONDA_NUMBER_CHANNEL_NOTICES=0`, `CONDA_AGGRESSIVE_UPDATE_PACKAGES=""`,
  `CONDA_LOCAL_REPODATA_TTL=300`, `CONDA_JSON=true`.
- Comprehensive test suite with pytest, pytest-benchmark, pytest-cov,
  and httpx for async API testing. 99% code coverage with a 95%
  `fail_under` threshold enforced on every run.
- Hyperfine benchmark fixtures (`benchmarks/`) for end-to-end CLI
  performance tracking.
- GitHub Actions CI workflow with lint, test, and benchmark jobs.
- Dependabot configuration for GitHub Actions version updates.
- BSD 3-Clause license.

[0.2.1]: https://github.com/jezdez/conda-resolve/releases/tag/v0.2.1
[0.2.0]: https://github.com/jezdez/conda-resolve/releases/tag/v0.2.0
[0.1.1]: https://github.com/jezdez/conda-resolve/releases/tag/v0.1.1
[0.1.0]: https://github.com/jezdez/conda-resolve/releases/tag/v0.1.0
