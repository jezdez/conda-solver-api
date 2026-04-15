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
    - ``_run_solver`` is protected by ``_solver_lock`` so that
      concurrent threads (from ``anyio.to_thread``) cannot race on the
      process-global ``os.environ`` and ``context`` state.
    - Solver errors are caught and returned as error strings in the
      ``SolveResult`` rather than propagated as exceptions, preventing
      internal stack traces from leaking to callers.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from operator import attrgetter

from conda.base.context import context
from conda.core.subdir_data import SubdirData
from conda.exceptions import PackagesNotFoundError, UnsatisfiableError
from conda.models.channel import Channel
from conda.models.environment import Environment
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
_solver_lock = threading.Lock()
_context_configured = False


def configure_platform(platform: str):
    """Set CONDA_SUBDIR and virtual package overrides for the target platform.

    This must be called before creating the solver so that conda
    generates the correct virtual packages (``__glibc``, ``__linux``,
    ``__osx``, etc.) for the target platform rather than the host.

    Callers must hold ``_solver_lock`` when calling this function,
    since it mutates process-global state (``os.environ``, conda
    ``context``).

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
    global _context_configured
    if _context_configured:
        return
    context.json = True
    _context_configured = True


def _run_solver(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> list[PackageRecord]:
    """Run the conda solver and return raw ``PackageRecord`` objects.

    Holds ``_solver_lock`` while configuring platform/context and
    running the solver to prevent concurrent threads from clobbering
    the process-global ``os.environ`` and conda ``context``.

    Returns records sorted by name.  Raises on solver failure so
    callers can handle errors in their own way.
    """
    with _solver_lock:
        configure_platform(platform)
        configure_context()

        specs = [MatchSpec(dep) for dep in dependencies]

        solver_backend = context.plugin_manager.get_cached_solver_backend()
        if solver_backend is None:
            raise RuntimeError("No solver backend found")

        solver = solver_backend(
            prefix="/env/does/not/exist",
            channels=channels or context.channels,
            subdirs=(platform, "noarch"),
            specs_to_add=specs,
            command="create",
        )

        records = solver.solve_final_state()
    return sorted(records, key=attrgetter("name"))


def _dispatch[T](
    solver_fn: Callable[[tuple[str, ...], list[str], str], T],
    channels: tuple[str, ...],
    dependencies: list[str],
    platforms: list[str],
    *,
    on_error: Callable[[str, Exception], T] | None = None,
) -> list[T]:
    """Dispatch *solver_fn* across *platforms*, preserving order.

    Single-platform solves run in-process; multi-platform solves are
    dispatched to the persistent process pool.  When *on_error* is
    provided, exceptions from individual platforms are caught and
    converted via ``on_error(platform, exc)``; otherwise they
    propagate.
    """
    if len(platforms) == 1:
        if on_error is not None:
            try:
                return [solver_fn(channels, dependencies, platforms[0])]
            except Exception as exc:
                return [on_error(platforms[0], exc)]
        return [solver_fn(channels, dependencies, platforms[0])]

    results: dict[str, T] = {}
    pool = get_process_pool()
    futures = {
        pool.submit(solver_fn, channels, dependencies, p): p
        for p in platforms
    }
    for future in as_completed(futures):
        platform = futures[future]
        try:
            results[platform] = future.result()
        except Exception as exc:
            if on_error is not None:
                results[platform] = on_error(platform, exc)
            else:
                raise

    return [results[p] for p in platforms]


# ---------------------------------------------------------------------------
# Server path — lightweight wrapper types for fast JSON and low memory
# ---------------------------------------------------------------------------


def solve_one_platform(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> SolveResult:
    """Solve for a single platform, returning a ``SolveResult``.

    Used by the HTTP API.  Wraps solver output in lightweight
    ``ResolvedPackage`` objects for fast serialization and low memory.
    """
    try:
        records = _run_solver(channels, dependencies, platform)
    except (
        UnsatisfiableError,
        PackagesNotFoundError,
        RuntimeError,
    ) as exc:
        return SolveResult(platform=platform, packages=[], error=str(exc))
    except Exception as exc:
        log.warning("Solver error for %s: %s", platform, exc)
        return SolveResult(platform=platform, packages=[], error=str(exc))

    packages = [ResolvedPackage.from_record(r) for r in records]
    return SolveResult(platform=platform, packages=packages)


def _solve_result_error(platform: str, exc: Exception) -> SolveResult:
    """Wrap an exception as a ``SolveResult`` with an error message."""
    log.warning("Solver dispatch error for %s: %s", platform, exc)
    return SolveResult(platform=platform, packages=[], error=str(exc))


def solve(
    channels: list[str],
    dependencies: list[str],
    platforms: list[str] | None = None,
) -> list[SolveResult]:
    """Solve for one or more platforms, returning ``SolveResult`` objects.

    Used by the HTTP API.  Single-platform solves run in-process;
    multi-platform solves are dispatched to a persistent process pool.
    Errors are captured per-platform rather than raised.
    """
    return _dispatch(
        solve_one_platform,
        tuple(channels),
        dependencies,
        platforms or [context.subdir],
        on_error=_solve_result_error,
    )


# ---------------------------------------------------------------------------
# CLI path — conda-native Environment objects, no intermediate types
# ---------------------------------------------------------------------------


def solve_one_environment(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> Environment:
    """Solve for a single platform, returning a conda ``Environment``.

    Used by the CLI.  Keeps ``PackageRecord`` objects from the solver
    without conversion, so conda's exporter plugins can use them
    directly.  Raises on solver failure.
    """
    records = _run_solver(channels, dependencies, platform)
    return Environment(
        platform=platform,
        explicit_packages=records,
    )


def solve_environments(
    channels: list[str],
    dependencies: list[str],
    platforms: list[str] | None = None,
) -> list[Environment]:
    """Solve for one or more platforms, returning ``Environment`` objects.

    Used by the CLI.  Multi-platform solves are dispatched to the
    process pool.  Errors propagate as exceptions.
    """
    return _dispatch(
        solve_one_environment,
        tuple(channels),
        dependencies,
        platforms or [context.subdir],
    )


_process_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def get_process_pool() -> ProcessPoolExecutor:
    """Return a persistent process pool, creating it on first use.

    Uses double-checked locking to be safe under concurrent requests
    from the async server.  Workers survive across requests so their
    in-memory SubdirData and solver index caches accumulate, making
    repeated solves faster.

    ``max_workers`` is capped at 4 or the CPU count, whichever is
    smaller — 4 is enough for the common 3-platform case and avoids
    over-subscribing on smaller machines.
    """
    global _process_pool
    if _process_pool is not None:
        return _process_pool
    with _pool_lock:
        if _process_pool is None:
            workers = min(4, os.cpu_count() or 4)
            _process_pool = ProcessPoolExecutor(max_workers=workers)
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
