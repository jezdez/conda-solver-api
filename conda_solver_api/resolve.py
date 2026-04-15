"""Core resolving logic using conda's solver API."""
from __future__ import annotations

import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from operator import attrgetter

import yaml
from conda.base.context import context
from conda.core.subdir_data import SubdirData
from conda.exceptions import UnsatisfiableError
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord


@dataclass
class SolveRequest:
    """Input for a solve operation, matching the shape of environment.yml."""

    channels: list[str] = field(default_factory=lambda: ["defaults"])
    dependencies: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)

    @classmethod
    def from_environment_yml(
        cls,
        source: str | bytes,
        *,
        channels: list[str] | None = None,
        platforms: list[str] | None = None,
    ) -> SolveRequest:
        """Parse an environment.yml and return a SolveRequest.

        *channels* and *platforms*, when given, override the values
        found in the YAML.
        """
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Expected a YAML mapping")

        deps = data.get("dependencies", [])
        conda_deps = [d for d in deps if isinstance(d, str)]

        return cls(
            channels=channels or data.get("channels", ["defaults"]),
            dependencies=conda_deps,
            platforms=platforms or [],
        )


@dataclass(slots=True)
class ResolvedPackage:
    """A single resolved package with its metadata."""

    name: str
    version: str
    build: str
    build_number: int
    channel: str
    subdir: str
    url: str
    sha256: str
    md5: str
    size: int | None
    depends: tuple[str, ...]
    constrains: tuple[str, ...]

    @classmethod
    def from_record(cls, record: PackageRecord) -> ResolvedPackage:
        return cls(
            name=record.name,
            version=str(record.version),
            build=record.build,
            build_number=record.build_number,
            channel=record.channel.canonical_name if record.channel else "",
            subdir=record.subdir or "",
            url=record.url or "",
            sha256=record.sha256 or "",
            md5=record.md5 or "",
            size=getattr(record, "size", None),
            depends=tuple(record.depends) if record.depends else (),
            constrains=tuple(record.constrains) if record.constrains else (),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "build": self.build,
            "build_number": self.build_number,
            "channel": self.channel,
            "subdir": self.subdir,
            "url": self.url,
            "sha256": self.sha256,
            "md5": self.md5,
            "size": self.size,
            "depends": list(self.depends),
            "constrains": list(self.constrains),
        }


@dataclass(slots=True)
class SolveResult:
    """The result of a solve operation for a single platform."""

    platform: str
    packages: list[ResolvedPackage]
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "packages": [p.to_dict() for p in self.packages],
            "error": self.error,
        }


def configure_context():
    """Set conda context options for fast, quiet solves.

    Expects CONDA_JSON=true in the environment (set via pixi activation).
    """
    context.json = True


def solve_one_platform(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> SolveResult:
    """Solve one platform. Safe for child processes (plain strings)."""
    configure_context()

    specs = [MatchSpec(dep) for dep in dependencies]

    solver_backend = context.plugin_manager.get_cached_solver_backend()
    if solver_backend is None:
        return SolveResult(
            platform=platform, packages=[], error="No solver backend found"
        )

    solver = solver_backend(
        prefix="/env/does/not/exist",
        channels=channels or context.channels,
        subdirs=(platform, "noarch"),
        specs_to_add=specs,
        command="create",
    )

    try:
        records = solver.solve_final_state()
    except UnsatisfiableError as exc:
        return SolveResult(platform=platform, packages=[], error=str(exc))
    except Exception as exc:
        return SolveResult(platform=platform, packages=[], error=str(exc))

    packages = [
        ResolvedPackage.from_record(r)
        for r in sorted(records, key=attrgetter("name"))
    ]
    return SolveResult(platform=platform, packages=packages)


def solve(request: SolveRequest) -> list[SolveResult]:
    """Solve an environment specification and return resolved packages per platform.

    Builds a conda Environment model from the request, solves it using the
    pluggable solver backend, and returns results with SHA256 hashes.
    No environment is created on disk.

    When multiple platforms are requested, solves run in parallel processes
    to bypass the GIL.
    """
    configure_context()

    platforms = request.platforms or [context.subdir]
    channels = tuple(request.channels)

    if len(platforms) == 1:
        return [solve_one_platform(channels, request.dependencies, platforms[0])]

    results: dict[str, SolveResult] = {}
    pool = get_process_pool()
    futures = {
        pool.submit(
            solve_one_platform, channels, request.dependencies, platform
        ): platform
        for platform in platforms
    }
    for future in as_completed(futures):
        platform = futures[future]
        try:
            results[platform] = future.result()
        except Exception as exc:
            results[platform] = SolveResult(
                platform=platform, packages=[], error=str(exc)
            )

    return [results[p] for p in platforms]


_process_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def get_process_pool() -> ProcessPoolExecutor:
    """Return a persistent process pool, creating it on first use.

    Workers survive across requests so their in-memory SubdirData and
    solver index caches accumulate, making repeated solves faster.
    """
    global _process_pool
    if _process_pool is not None:
        return _process_pool
    with _pool_lock:
        if _process_pool is None:
            _process_pool = ProcessPoolExecutor(max_workers=4)
        return _process_pool


def _warmup_subdirs(channels: list[str], platforms: list[str]):
    """Load SubdirData for each channel/subdir to populate on-disk cache."""
    configure_context()
    subdirs = list(dict.fromkeys(
        subdir
        for platform in platforms
        for subdir in (platform, "noarch")
    ))
    for channel_name in channels:
        for subdir in subdirs:
            SubdirData(Channel(f"{channel_name}/{subdir}")).load()


def warmup(channels: list[str], platforms: list[str]):
    """Pre-warm caches for both the parent process and worker pool.

    Warms the on-disk repodata cache in the parent, then submits a
    no-op to each worker so they fork with warm interpreter state.
    """
    _warmup_subdirs(channels, platforms)

    pool = get_process_pool()
    futures = [
        pool.submit(_warmup_subdirs, channels, [p]) for p in platforms
    ]
    for f in futures:
        f.result()
