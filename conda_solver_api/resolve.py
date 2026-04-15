"""Core resolving logic using conda's solver API.

This module performs dry-run solves: it resolves a set of package specs
against conda channels and returns fully-pinned package metadata
(versions, builds, SHA256 hashes, URLs) without downloading or
installing anything.

Performance notes:
    - Multi-platform solves run in a persistent ``ProcessPoolExecutor``
      to bypass the GIL. Workers retain their in-memory ``SubdirData``
      and solver index caches across requests.
    - All arguments to ``solve_one_platform`` are plain strings/tuples
      so they serialize cheaply for cross-process dispatch.
    - ``ResolvedPackage`` uses ``slots=True`` and stores depends/constrains
      as tuples (immutable, smaller than lists). Conversion to list
      happens only at the serialization boundary in ``to_dict()``.
    - ``to_dict()`` is hand-written instead of using ``dataclasses.asdict()``
      which performs a recursive deep-copy of all nested structures.

Security notes:
    - ``from_environment_yml`` uses ``yaml.safe_load`` (never ``yaml.load``)
      to prevent arbitrary code execution from untrusted YAML input.
    - Solver errors are caught and returned as error strings in the
      ``SolveResult`` rather than propagated as exceptions, preventing
      internal stack traces from leaking to callers.
"""
from __future__ import annotations

import logging
import os
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

log = logging.getLogger(__name__)

# Default virtual package versions for cross-platform solving.
# When solving for linux-64 from macOS (or vice versa), conda needs
# virtual packages like __glibc and __linux to be present. These
# defaults represent a recent, widely-compatible Linux system.
VIRTUAL_PACKAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "linux": {
        "CONDA_OVERRIDE_GLIBC": "2.35",
        "CONDA_OVERRIDE_LINUX": "6.1",
    },
    "osx": {
        "CONDA_OVERRIDE_OSX": "14.0",
    },
}


_current_platform: str | None = None


def configure_platform(platform: str):
    """Set CONDA_SUBDIR and virtual package overrides for the target platform.

    This must be called before creating the solver so that conda
    generates the correct virtual packages (``__glibc``, ``__linux``,
    ``__osx``, etc.) for the target platform rather than the host.

    Skips re-initialization when the platform has not changed (common
    in server workloads that repeatedly solve for the same target).
    Only sets overrides if not already present in the environment,
    allowing callers to provide their own values.
    """
    global _current_platform
    if _current_platform == platform:
        return

    os.environ["CONDA_SUBDIR"] = platform

    for prefix, overrides in VIRTUAL_PACKAGE_DEFAULTS.items():
        if platform.startswith(prefix):
            for key, default in overrides.items():
                os.environ.setdefault(key, default)
            break

    context.__init__()
    _current_platform = platform


@dataclass
class SolveRequest:
    """Input for a solve operation, matching the shape of environment.yml.

    Attributes:
        channels: Conda channels to search, highest priority first.
        dependencies: Package specs (e.g. ``["python=3.12", "numpy"]``).
        platforms: Target subdirs (e.g. ``["linux-64", "osx-arm64"]``).
            When empty, defaults to the current platform at solve time.
    """

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

        Only conda dependencies (plain strings) are extracted; pip
        sub-dicts are silently skipped.  *channels* and *platforms*,
        when given, override the values found in the YAML.

        Raises:
            ValueError: On invalid YAML or unexpected document structure.
        """
        # safe_load prevents arbitrary Python object instantiation
        # from untrusted YAML input
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
    """A single resolved package with its metadata.

    Uses ``slots=True`` to eliminate per-instance ``__dict__`` overhead
    (~200 bytes saved per instance).  ``depends`` and ``constrains`` are
    stored as tuples (immutable, no over-allocation for growth).
    """

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
        """Convert a conda ``PackageRecord`` to a ``ResolvedPackage``.

        Extracts only the fields needed for the API response.
        ``getattr`` is used for ``size`` because auxlib Entity fields
        raise ``AttributeError`` when unset rather than returning None.
        """
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
        """Serialize to a plain dict for JSON output.

        Hand-written instead of ``dataclasses.asdict()`` to avoid its
        recursive deep-copy, which creates hundreds of temporary
        objects for a typical solve result.
        """
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
    """The result of a solve operation for a single platform.

    On solver failure, ``packages`` is empty and ``error`` contains a
    sanitized error message (no internal paths or stack traces).
    """

    platform: str
    packages: list[ResolvedPackage]
    error: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return {
            "platform": self.platform,
            "packages": [p.to_dict() for p in self.packages],
            "error": self.error,
        }


def configure_context():
    """Set conda context options for fast, quiet solves.

    ``context.json = True`` switches conda to its JSON output mode,
    which uses a no-op spinner and suppresses progress messages that
    would otherwise pollute stdout.

    Also expected via environment: ``CONDA_JSON=true`` (set via pixi
    activation) so child processes inherit the setting.
    """
    context.json = True


def solve_one_platform(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> SolveResult:
    """Solve for a single platform.

    All parameters are plain strings so this function can be dispatched
    to a ``ProcessPoolExecutor`` worker without serialization issues.

    Calls ``configure_platform`` to set CONDA_SUBDIR and inject
    virtual package overrides (``__glibc``, ``__linux``, ``__osx``)
    so cross-platform solves succeed. Uses ``command="create"`` with
    a non-existent prefix so the solver treats this as a fresh
    environment.
    """
    configure_platform(platform)
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
        log.warning("Solver error for %s: %s", platform, exc)
        return SolveResult(platform=platform, packages=[], error=str(exc))

    # attrgetter is a C-level callable, faster than a Python lambda
    packages = [
        ResolvedPackage.from_record(r)
        for r in sorted(records, key=attrgetter("name"))
    ]
    return SolveResult(platform=platform, packages=packages)


def solve(request: SolveRequest) -> list[SolveResult]:
    """Solve an environment specification for one or more platforms.

    Single-platform requests run in-process to avoid IPC overhead.
    Multi-platform requests are dispatched to a persistent process
    pool for true parallelism (bypasses the GIL).

    Results are returned in the same order as ``request.platforms``.
    """
    platforms = request.platforms or [context.subdir]
    channels = tuple(request.channels)

    if len(platforms) == 1:
        return [
            solve_one_platform(channels, request.dependencies, platforms[0])
        ]

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

    Uses double-checked locking to be safe under concurrent requests
    from the async server.  Workers survive across requests so their
    in-memory SubdirData and solver index caches accumulate, making
    repeated solves faster.
    """
    global _process_pool
    if _process_pool is not None:
        return _process_pool
    with _pool_lock:
        if _process_pool is None:
            _process_pool = ProcessPoolExecutor(max_workers=4)
        return _process_pool


def _warmup_subdirs(channels: list[str], platforms: list[str]):
    """Load SubdirData for each channel/subdir to populate on-disk cache.

    This forces conda to fetch and cache repodata so subsequent solves
    don't pay the network I/O cost.
    """
    if platforms:
        configure_platform(platforms[0])
    configure_context()
    # dict.fromkeys preserves insertion order and deduplicates
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

    Warms the on-disk repodata cache in the parent process first,
    then dispatches warmup tasks to each worker process so they
    build their own in-memory SubdirData caches.  This ensures the
    first real solve request doesn't pay the full cold-start cost.
    """
    _warmup_subdirs(channels, platforms)

    pool = get_process_pool()
    futures = [
        pool.submit(_warmup_subdirs, channels, [p]) for p in platforms
    ]
    for f in futures:
        f.result()
