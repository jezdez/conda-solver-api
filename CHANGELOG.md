# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-15

### Added

- Core solver module (`resolve.py`) with `SolveRequest`, `ResolvedPackage`,
  and `SolveResult` dataclasses using `slots=True` for memory efficiency.
- `SolveRequest.from_environment_yml()` for parsing environment files
  with `yaml.safe_load` (secure by default).
- Multi-platform parallel solving via `ProcessPoolExecutor` with
  persistent worker pool for cross-request cache reuse.
- Cross-platform virtual package injection (`configure_platform()`)
  that sets `CONDA_SUBDIR` and `CONDA_OVERRIDE_*` defaults so solves
  for linux-64 from macOS (and vice versa) work correctly.
- HTTP API (`app.py`) built on Starlette with `/solve`,
  `/solve/environment-yml`, and `/health` endpoints.
- Request body size limit (1 MB), input type validation, and sanitized
  error responses (no internal stack traces leaked to clients).
- Repodata cache pre-warming on server startup via `warmup()`,
  offloaded from the event loop with `anyio.to_thread.run_sync`.
- CLI with `solve` and `serve` subcommands.
- Conda subcommand plugin (`conda solver-api`) with lazy imports
  to keep plugin load under 1 ms.
- Performance tuning via pixi activation env: `CONDA_SOLVER=rattler`,
  `CONDA_REPODATA_THREADS=4`, `CONDA_CHANNEL_PRIORITY=strict`,
  `CONDA_NO_LOCK=true`, `CONDA_UNSATISFIABLE_HINTS=false`,
  `CONDA_NUMBER_CHANNEL_NOTICES=0`, `CONDA_AGGRESSIVE_UPDATE_PACKAGES=""`,
  `CONDA_LOCAL_REPODATA_TTL=300`, `CONDA_JSON=true`.
- Comprehensive test suite (66 tests) with pytest, pytest-benchmark,
  and httpx for async API testing.
- Hyperfine benchmark fixtures (`benchmarks/`) for end-to-end CLI
  performance tracking.
- GitHub Actions CI workflow with lint, test, and benchmark jobs.
- Dependabot configuration for GitHub Actions version updates.
- BSD 3-Clause license.

[0.1.0]: https://github.com/jezdez/conda-solver-api/commits/main
