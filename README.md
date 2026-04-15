# conda-solver-api

A fast, dry-run conda solver exposed as both a CLI and an HTTP API.
Given package specs or an `environment.yml`, it resolves fully pinned
packages (with SHA256 hashes, URLs, and dependency metadata) for one
or more platforms — without downloading or installing anything.

It registers as a conda subcommand plugin (`conda solver-api`) and
can also run as a standalone HTTP service for integration into
CI pipelines, security scanners, or other tooling that needs resolved
package lists programmatically.

## Features

- Resolve inline specs or `environment.yml` files
- Cross-platform solving (e.g. solve for `linux-64` from macOS)
- Multi-platform solves run in parallel via `ProcessPoolExecutor`
- HTTP API with JSON input/output (Starlette + uvicorn)
- Repodata cache pre-warming on server startup
- Conda plugin: `conda solver-api solve` / `conda solver-api serve`
- Tuned for speed: strict channel priority, parallel repodata
  fetching, no lock contention, repodata TTL caching

## Install

Set up with [pixi](https://pixi.sh):

```bash
pixi install
```

Or install manually into an existing conda environment:

```bash
conda install conda-rattler-solver
pip install -e .
```

Requires conda >= 25.3 and Python >= 3.13.

## Usage

### As a conda subcommand

```bash
conda solver-api solve -c conda-forge -p linux-64 python=3.12 numpy

conda solver-api solve -f environment.yml -p linux-64 -p osx-arm64

conda solver-api serve --port 8000
```

### As a standalone CLI

```bash
conda-solver-api solve -c conda-forge -p linux-64 zlib

conda-solver-api solve -f environment.yml

conda-solver-api serve
```

### Output format

JSON array with one entry per platform:

```json
[
  {
    "platform": "linux-64",
    "packages": [
      {
        "name": "numpy",
        "version": "2.2.4",
        "build": "py312h72c5963_0",
        "build_number": 0,
        "channel": "conda-forge",
        "subdir": "linux-64",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/numpy-2.2.4-py312h72c5963_0.conda",
        "sha256": "...",
        "md5": "...",
        "size": 8048579,
        "depends": ["libblas >=3.9.0,<4.0a0", "..."],
        "constrains": []
      }
    ],
    "error": null
  }
]
```

## HTTP API

Start the server:

```bash
conda solver-api serve
# or: uvicorn conda_solver_api.app:app
```

### `POST /solve`

```bash
curl -X POST http://localhost:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{"channels": ["conda-forge"], "dependencies": ["python=3.12", "numpy"], "platforms": ["linux-64"]}'
```

### `POST /solve/environment-yml`

Send the YAML as the request body, specify platforms via query params:

```bash
curl -X POST 'http://localhost:8000/solve/environment-yml?platform=linux-64' \
  -H 'Content-Type: application/x-yaml' \
  --data-binary @environment.yml
```

### `GET /health`

Returns `{"status": "ok"}`.

## Development

```bash
pixi run lint        # ruff check
pixi run format      # ruff format
pixi run test        # pytest (benchmarks disabled)
pixi run bench       # pytest-benchmark only
pixi run serve       # uvicorn with --reload
```

## Performance tuning

The following conda environment variables are set via pixi activation
to optimize for a solve-only workload:

| Variable | Value | Purpose |
|---|---|---|
| `CONDA_REPODATA_THREADS` | `4` | Parallel repodata fetching |
| `CONDA_CHANNEL_PRIORITY` | `strict` | Skip lower-priority channels early |
| `CONDA_NO_LOCK` | `true` | Skip filesystem locking (single-writer) |
| `CONDA_UNSATISFIABLE_HINTS` | `false` | Skip expensive hint generation on failure |
| `CONDA_NUMBER_CHANNEL_NOTICES` | `0` | No channel notices |
| `CONDA_AGGRESSIVE_UPDATE_PACKAGES` | `""` | No forced updates |
| `CONDA_LOCAL_REPODATA_TTL` | `300` | 5-minute repodata cache TTL |
| `CONDA_JSON` | `true` | Suppress progress output |

The server pre-warms repodata caches on startup (both the parent
process and `ProcessPoolExecutor` workers) so the first request
doesn't pay the full repodata fetch cost.

## Benchmarks

Measured with `pytest-benchmark` on macOS ARM64, Python 3.13,
conda 26.3.2 (canary/dev), conda-rattler-solver, warm repodata cache:

### Serialization (dataclass operations)

| Operation | Time | Throughput |
|---|---|---|
| `ResolvedPackage.from_record` (single) | 2.5 µs | 406k ops/s |
| `ResolvedPackage.from_record` (100 batch) | 256 µs | 3.9k batches/s |
| `ResolvedPackage.to_dict` (single) | 293 ns | 3.4M ops/s |
| `to_dict` (100-package batch) | 22 µs | 45k batches/s |
| `SolveResult.to_dict` (100 packages) | 23 µs | 44k ops/s |

### Solver (warm cache, conda-forge)

| Operation | Time |
|---|---|
| Single-platform solve (`zlib`) | 74 ms |
| Single-platform solve (`python=3.12, numpy`) | 241 ms |
| Multi-platform solve (`zlib`, 2 platforms) | 78 ms |

Run benchmarks:

```bash
pixi run bench
```
