# conda-resolve

A fast, dry-run conda solver exposed as both a CLI and an HTTP API.
Given package specs or an `environment.yml`, it resolves fully pinned
packages (with SHA256 hashes, URLs, and dependency metadata) for one
or more platforms — without downloading or installing anything.

It registers as a conda subcommand plugin (`conda resolve`) and
can also run as a standalone HTTP service for integration into
CI pipelines, security scanners, or other tooling that needs resolved
package lists programmatically.

## Features

- Resolve inline specs or environment files (`.yml`, `.yaml`, `.txt`,
  `.lock`, `.toml`, `.json`)
- Full package metadata by default: sha256, md5, urls, sizes, depends
- Cross-platform solving (e.g. solve for `linux-64` from macOS) with
  automatic virtual package injection (`__glibc`, `__linux`, `__osx`)
- Multi-platform solves run in parallel via `ProcessPoolExecutor`
- Multiple output formats via conda exporter plugins (`--format`)
- Multiple `--file` inputs merged into a single solve
- Conda-native CLI flags (`--override-channels`, `--solver`, `--offline`, etc.)
- HTTP API with JSON input/output (Starlette + uvicorn)
- Repodata index caching with TTL (300s) for ~50x faster repeat solves
- Conda plugin: `conda resolve` / `conda resolve --serve`
- Uses `conda-rattler-solver` for fast SAT solving

## Install

Install globally with [pixi](https://pixi.sh):

```bash
pixi global install --git https://github.com/jezdez/conda-resolve.git
```

For development, clone the repo and install locally:

```bash
git clone https://github.com/jezdez/conda-resolve.git
cd conda-resolve
pixi install
```

Requires conda >= 25.3 and Python >= 3.13.

## Usage

### As a conda subcommand

```bash
conda resolve -c conda-forge -p linux-64 python=3.12 numpy

conda resolve -f environment.yml -p linux-64 -p osx-arm64

conda resolve -c conda-forge -p linux-64 --format explicit zlib

conda resolve --serve --port 8000
```

### Output formats

**resolve-json** (default): full package metadata with sha256, urls,
sizes, depends, and constrains.

```bash
conda resolve -c conda-forge -p linux-64 zlib
```

```json
{
  "platform": "linux-64",
  "packages": [
    {
      "name": "zlib",
      "version": "1.3.2",
      "build": "h25fd6f3_2",
      "build_number": 2,
      "channel": "conda-forge",
      "subdir": "linux-64",
      "url": "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda",
      "sha256": "245c9ee8d688e23661b95e3c6dd7272ca936fabc03d423cdb3cdee1bbcf9f2f2",
      "md5": "c2a01a08fc991620a74b32420e97868a",
      "size": 95931,
      "depends": ["__glibc >=2.17,<3.0.a0", "libzlib 1.3.2 h25fd6f3_2"],
      "constrains": []
    }
  ]
}
```

**Explicit lockfile** (`--format explicit`): one URL per line,
compatible with `conda create --file`.

```bash
conda resolve -c conda-forge -p linux-64 --format explicit zlib
```

**YAML** (`--format yaml`): conda environment.yml format.

```bash
conda resolve -c conda-forge -p linux-64 --format yaml zlib
```

Other formats can be provided by conda exporter plugins
(`--format <name>`).

## HTTP API

Start the server:

```bash
conda resolve --serve
# or: uvicorn conda_resolve.app:app
```

### `GET /resolve`

Resolve inline specs via query params:

```bash
curl 'http://localhost:8000/resolve?spec=python=3.12&spec=numpy&channel=conda-forge&platform=linux-64'
```

### `POST /resolve`

Resolve specs and/or file content via JSON body:

```bash
curl -X POST http://localhost:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"specs": ["python=3.12", "numpy"], "channels": ["conda-forge"], "platforms": ["linux-64"]}'
```

Send environment file content:

```bash
curl -X POST http://localhost:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"file": "name: env\nchannels:\n  - conda-forge\ndependencies:\n  - scipy\n", "platforms": ["linux-64"]}'
```

Query params (`spec`, `channel`, `platform`) work on both GET and POST.
Body fields override query params when both are present.

Returns a JSON array with one entry per platform, same structure as
the CLI's `resolve-json` format.

### `GET /health`

Returns `{"status": "ok"}`.

### `GET /openapi.json`

Returns the OpenAPI 3.1 schema describing all endpoints.

## Docker

Two image flavors are published to GitHub Container Registry on every
release, for both `linux/amd64` and `linux/arm64`:

- **Server** (`latest`) — starts the HTTP API by default
- **CLI** (`cli`) — runs `conda resolve` directly, pass args after the image name

### Server image

```bash
docker run -p 8000:8000 ghcr.io/jezdez/conda-resolve:latest
```

The first startup takes ~20-30s while the repodata cache warms up.
Subsequent solves use the in-memory cache and return in milliseconds.

```bash
curl -X POST http://localhost:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"specs": ["python=3.13"], "platforms": ["linux-64", "osx-arm64"]}'
```

### CLI image

```bash
docker run ghcr.io/jezdez/conda-resolve:cli -c conda-forge -p linux-64 zlib

docker run ghcr.io/jezdez/conda-resolve:cli -f environment.yml -p linux-64
```

### Available tags

| Tag | Image | Description |
|---|---|---|
| `latest` | Server | Most recent server release |
| `<version>` | Server | Specific release (e.g. `0.2.0`) |
| `<major>.<minor>` | Server | Latest patch for a minor (e.g. `0.2`) |
| `<major>` | Server | Latest minor for a major (e.g. `0`) |
| `cli` | CLI | Most recent CLI release |
| `<version>-cli` | CLI | Specific CLI release (e.g. `0.2.0-cli`) |
| `<major>.<minor>-cli` | CLI | Latest CLI patch for a minor |

### Building locally

```bash
docker build -f docker/server.Dockerfile -t conda-resolve .
docker run -p 8000:8000 conda-resolve

docker build -f docker/cli.Dockerfile -t conda-resolve-cli .
docker run conda-resolve-cli -c conda-forge -p linux-64 zlib
```

Both images use a multi-stage build: dependencies are installed with
pixi in the build stage, and only the runtime environment is copied
into a minimal `debian:bookworm-slim` image. Both run as a non-root
user.

## Development

```bash
pixi run lint        # ruff check
pixi run format      # ruff format
pixi run test        # pytest (benchmarks disabled)
pixi run bench       # pytest-benchmark only
pixi run serve       # uvicorn with --reload
```

## Performance

### Index caching

Building the repodata index (~700 ms) is the dominant cost of a solve.
The solver caches `RattlerIndexHelper` objects keyed by
`(channels, platform)` with a 300-second TTL. After the first solve,
repeat solves for the same channels/platform hit the cache and only
pay the SAT solving time (~20-100 ms).

The server pre-warms these caches on startup for the configured
default channels and platforms (see environment variables below).

### Environment variables

#### Application

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_RESOLVE_CHANNELS` | `conda-forge` | Comma-separated default channels when none are specified in a request. Also used for cache warmup. |
| `CONDA_RESOLVE_PLATFORMS` | `linux-64,osx-arm64,osx-64` | Comma-separated platforms to pre-warm repodata caches for on startup. |
| `CONDA_RESOLVE_CONCURRENCY` | `4` | Maximum concurrent solve requests (thread limiter). |
| `CONDA_RESOLVE_WORKERS` | `min(4, cpu_count)` | Process pool size for multi-platform parallel solves. |
| `CONDA_RESOLVE_MAX_BODY_BYTES` | `1048576` (1 MB) | Maximum allowed request body size in bytes. |
| `CONDA_RESOLVE_HOST` | `127.0.0.1` | Default bind address for `--serve` / `--host`. |
| `CONDA_RESOLVE_PORT` | `8000` | Default port for `--serve` / `--port`. |
| `CONDA_RESOLVE_GLIBC_VERSION` | `2.17` | Virtual `__glibc` version for cross-platform Linux solves. |
| `CONDA_RESOLVE_LINUX_VERSION` | `5.15` | Virtual `__linux` version for cross-platform Linux solves. |
| `CONDA_RESOLVE_OSX_VERSION` | `11.0` | Virtual `__osx` version for cross-platform macOS solves. |

#### Conda tuning

The following conda environment variables are set via pixi activation
to optimize for a solve-only workload:

| Variable | Value | Purpose |
|---|---|---|
| `CONDA_SOLVER` | `rattler` | Use the fast rattler solver backend |
| `CONDA_CHANNEL_PRIORITY` | `strict` | Skip lower-priority channels early |
| `CONDA_NO_LOCK` | `true` | Skip filesystem locking (single-writer) |
| `CONDA_UNSATISFIABLE_HINTS` | `false` | Skip expensive hint generation on failure |
| `CONDA_NUMBER_CHANNEL_NOTICES` | `0` | No channel notices |
| `CONDA_AGGRESSIVE_UPDATE_PACKAGES` | `""` | No forced updates |
| `CONDA_LOCAL_REPODATA_TTL` | `300` | 5-minute repodata cache TTL |
| `CONDA_JSON` | `true` | Suppress progress output |

### Cross-platform virtual packages

When solving for a foreign platform (e.g. `linux-64` from macOS),
conda needs virtual packages (`__glibc`, `__linux`, `__osx`) to be
present for the target platform. The solver automatically injects
defaults via `context.override_virtual_packages`:

- **linux**: `__glibc` (default `2.17`, conda-forge baseline), `__linux` (default `5.15`)
- **osx**: `__osx` (default `11.0`, Big Sur, conda-forge arm64 baseline)

Override these via `CONDA_RESOLVE_GLIBC_VERSION`,
`CONDA_RESOLVE_LINUX_VERSION`, and `CONDA_RESOLVE_OSX_VERSION`
(see the application environment variables table above).

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

### In-process server (pytest-benchmark)

With a warm index cache, solves are dominated by SAT time only:

| Operation | Time |
|---|---|
| Single-platform solve (`zlib`) | ~17 ms |
| Single-platform solve (`python=3.12, numpy`) | ~106 ms |
| `ResolvedPackage.from_record` (single) | 2.5 µs |
| `ResolvedPackage.to_dict` (single) | 293 ns |
| `SolveResult.to_dict` (100 packages) | 23 µs |

Run benchmarks:

```bash
pixi run bench               # pytest-benchmark
hyperfine 'pixi run conda-resolve -c conda-forge -p linux-64 python=3.12 numpy'
```
