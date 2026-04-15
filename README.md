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
- Cross-platform solving (e.g. solve for `linux-64` from macOS) with
  automatic virtual package injection (`__glibc`, `__linux`, `__osx`)
- Multi-platform solves run in parallel via `ProcessPoolExecutor`
- Multiple output formats: JSON (default), explicit lockfile, text
- Multiple `--file` inputs merged into a single solve
- Conda-native CLI flags (`--override-channels`, `--solver`, `--offline`, etc.)
- HTTP API with JSON input/output (Starlette + uvicorn)
- Repodata cache pre-warming on server startup
- Conda plugin: `conda solver-api solve` / `conda solver-api serve`
- Uses `conda-rattler-solver` for fast SAT solving
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

conda solver-api solve -c conda-forge -p linux-64 --explicit --md5 zlib

conda solver-api serve --port 8000
```

### As a standalone CLI

```bash
conda-solver-api solve -c conda-forge -p linux-64 zlib

conda-solver-api solve -f env1.yml -f env2.yml -p linux-64

conda-solver-api solve --override-channels -c my-channel -p linux-64 numpy

conda-solver-api solve --solver rattler -c conda-forge -p linux-64 zlib

conda-solver-api serve
```

### Output formats

**JSON** (default): full package metadata with SHA256, URLs, dependencies.

**Explicit lockfile** (`--explicit`): one URL per line, compatible with
`conda create --file`. Add `--md5` to append MD5 hashes.

```bash
conda-solver-api solve -c conda-forge -p linux-64 --explicit --md5 zlib
```

**Text** (`--no-channels` and/or `--no-builds`): compact text listing.

```bash
conda-solver-api solve -c conda-forge -p linux-64 --no-channels --no-builds zlib
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
| `CONDA_SOLVER` | `rattler` | Use the fast rattler solver backend |
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

### Cross-platform virtual packages

When solving for a foreign platform (e.g. `linux-64` from macOS),
conda needs virtual packages (`__glibc`, `__linux`, `__osx`) to be
present for the target platform. The solver automatically injects
reasonable defaults via `CONDA_OVERRIDE_*` environment variables:

- **linux**: `__glibc=2.35`, `__linux=6.1`
- **osx**: `__osx=14.0`

Override these by setting the corresponding environment variables
before running a solve (e.g. `CONDA_OVERRIDE_GLIBC=2.17`).

## Benchmarks

### End-to-end CLI (hyperfine)

Measured with `hyperfine` on macOS ARM64, Python 3.13,
conda 26.3.2 (canary/dev), `conda-rattler-solver`, warm repodata
cache, solving against `conda-forge`:

| Scenario | Mean | Min | Max |
|---|---:|---:|---:|
| zlib, 1 platform | 1.2 s | 1.2 s | 1.2 s |
| zlib, 3 platforms | 1.5 s | 1.5 s | 1.6 s |
| py+scipy+pandas+matplotlib, 1 platform | 1.7 s | 1.7 s | 1.8 s |
| py+scipy+pandas+matplotlib, 3 platforms | 2.0 s | 2.0 s | 2.1 s |
| py+torch+transformers+sklearn (11 pkgs), 1 platform | 4.5 s | 4.4 s | 4.5 s |
| py+torch+transformers+sklearn (11 pkgs), 3 platforms | 5.2 s | 5.1 s | 5.3 s |

Times include Python startup (~50 ms), pixi overhead (~50 ms), and
conda import (~200 ms). Multi-platform solves run in parallel and
scale sub-linearly (3 platforms in ~1.2x the time of 1).

### In-process (pytest-benchmark)

| Operation | Time |
|---|---|
| `ResolvedPackage.from_record` (single) | 2.5 µs |
| `ResolvedPackage.to_dict` (single) | 293 ns |
| `SolveResult.to_dict` (100 packages) | 23 µs |
| Single-platform solve (`zlib`) | 74 ms |
| Single-platform solve (`python=3.12, numpy`) | 241 ms |

Run benchmarks:

```bash
pixi run bench               # pytest-benchmark
hyperfine 'pixi run -e test conda-solver-api solve -c conda-forge -p linux-64 python=3.12 numpy'
```
