"""Core resolving logic using conda's solver API.

This module performs dry-run solves: it resolves a set of package specs
against conda channels and returns fully-pinned package metadata
(versions, builds, SHA256 hashes, URLs) without downloading or
installing anything.

Performance notes:
    - ``build_index()`` caches ``RattlerIndexHelper`` objects keyed by
      ``(channels, platform)``.  Building an index (~700 ms) is the
      dominant cost of a solve; the cache reduces repeat solves to just
      the SAT time (~20-100 ms).  ``index_lock`` makes check-then-build
      atomic, preventing thundering-herd on cold or cleared caches.
      Call ``clear_index_cache()`` to invalidate all entries.
    - Multi-platform solves run in a persistent ``ProcessPoolExecutor``
      to bypass the GIL. Workers retain their own index caches across
      requests.
    - All arguments to ``solve_one_platform`` are plain strings/tuples
      so they serialize cheaply for cross-process dispatch.
    - ``ResolvedPackage`` uses ``slots=True`` and stores depends/constrains
      as tuples (immutable, smaller than lists). Conversion to list
      happens only at the serialization boundary in ``to_dict()``.
    - ``to_dict()`` is hand-written instead of using ``dataclasses.asdict()``
      which performs a recursive deep-copy of all nested structures.

Security notes:
    - ``run_solver`` is protected by ``platform_lock`` so that
      concurrent threads (from ``anyio.to_thread``) cannot race on the
      conda ``context`` singleton state.
    - Solver errors are caught and returned as error strings in the
      ``SolveResult`` rather than propagated as exceptions, preventing
      internal stack traces from leaking to callers.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from operator import attrgetter

from conda.base.context import context
from conda.exceptions import PackagesNotFoundError, UnsatisfiableError
from conda.models.environment import Environment
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord

from .config import GLIBC_VERSION, LINUX_VERSION, MAX_WORKERS, OSX_VERSION

log = logging.getLogger(__name__)

# Virtual package overrides for cross-platform solving.
# Keyed by platform prefix → {package_name: version}.
# Versions are configurable via CONDA_PRESTO_GLIBC_VERSION,
# CONDA_PRESTO_LINUX_VERSION, and CONDA_PRESTO_OSX_VERSION.
VIRTUAL_PACKAGES: dict[str, dict[str, str]] = {
    "linux": {"glibc": GLIBC_VERSION, "linux": LINUX_VERSION},
    "osx": {"osx": OSX_VERSION},
}

NATIVE_SUBDIR: str = context.subdir

current_platform: str | None = None
platform_lock = threading.Lock()
context_configured = False

index_lock = threading.Lock()
index_cache: dict[tuple[tuple[str, ...], str], object] = {}


def configure_platform(platform: str):
    """Point conda's context at *platform* for cross-platform solving.

    Sets ``context._subdir`` and ``context.override_virtual_packages``
    directly via the descriptor cache, avoiding ``os.environ`` mutation.
    This eliminates a class of thread-safety issues: env-var writes are
    process-global, but the cache is a plain dict on the singleton.

    Callers must hold ``platform_lock`` when calling this function,
    since the context singleton is shared across threads.

    Skips re-initialization when the platform has not changed.
    """
    global current_platform
    if current_platform == platform:
        return

    context._cache_["_subdir"] = platform

    overrides: dict[str, str] = {}
    for prefix, pkgs in VIRTUAL_PACKAGES.items():
        if platform.startswith(prefix):
            overrides = pkgs
            break
    context._cache_["_override_virtual_packages"] = overrides

    current_platform = platform


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

    Sets ``context.json`` (no-op spinner, no progress noise) and
    ensures the rattler solver backend is selected.  Both are set
    directly on the context singleton rather than via env vars.
    """
    global context_configured
    if context_configured:
        return
    context.json = True
    context._cache_["solver"] = "rattler"
    context_configured = True


def build_index(
    channels: tuple[str, ...],
    platform: str,
) -> object:
    """Return a cached ``RattlerIndexHelper``, building if absent.

    ``index_lock`` makes the check-then-build atomic, so only one
    thread ever builds a given index — no thundering herd.
    """
    from conda_rattler_solver.index import RattlerIndexHelper

    key = (channels, platform)
    with index_lock:
        cached = index_cache.get(key)
        if cached is not None:
            return cached
        log.debug("Building index for %s/%s", channels, platform)
        index = RattlerIndexHelper(
            channels=list(channels),
            subdirs=(platform, "noarch"),
        )
        index_cache[key] = index
        return index


def clear_index_cache():
    """Drop all cached indexes."""
    with index_lock:
        index_cache.clear()


def run_solver(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> list[PackageRecord]:
    """Run the conda solver and return raw ``PackageRecord`` objects.

    Holds ``platform_lock`` while configuring platform/context and
    running the solver, because the conda ``context`` singleton is
    shared across threads and the solver reads from it during
    construction and solving.

    Uses a cached ``RattlerIndexHelper`` so that repeated solves for
    the same channels/platform skip the ~700 ms index-build step.

    Returns records sorted by name.  Raises on solver failure so
    callers can handle errors in their own way.
    """
    from conda_rattler_solver.state import SolverInputState, SolverOutputState

    with platform_lock:
        configure_platform(platform)
        configure_context()

        specs = [MatchSpec(dep) for dep in dependencies]

        solver_backend = context.plugin_manager.get_cached_solver_backend()
        if solver_backend is None:
            raise RuntimeError("No solver backend found")

        index = build_index(channels, platform)

        solver = solver_backend(
            prefix="/env/does/not/exist",
            channels=channels or context.channels,
            subdirs=(platform, "noarch"),
            specs_to_add=specs,
            command="create",
        )

        in_state = SolverInputState(
            prefix=solver.prefix,
            requested=solver.specs_to_add,
            command="create",
        )
        out_state = SolverOutputState(solver_input_state=in_state)
        out_state = solver._solving_loop(in_state, out_state, index)
        records = list(out_state.current_solution)

    return sorted(records, key=attrgetter("name"))


def dispatch[T](
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
        records = run_solver(channels, dependencies, platform)
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


def solve_result_error(platform: str, exc: Exception) -> SolveResult:
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
    return dispatch(
        solve_one_platform,
        tuple(channels),
        dependencies,
        platforms or [NATIVE_SUBDIR],
        on_error=solve_result_error,
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
    records = run_solver(channels, dependencies, platform)
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
    return dispatch(
        solve_one_environment,
        tuple(channels),
        dependencies,
        platforms or [NATIVE_SUBDIR],
    )


process_pool: ProcessPoolExecutor | None = None
pool_lock = threading.Lock()


def get_process_pool() -> ProcessPoolExecutor:
    """Return a persistent process pool, creating it on first use.

    Uses double-checked locking to be safe under concurrent requests
    from the async server.  Workers survive across requests so their
    in-memory SubdirData and solver index caches accumulate, making
    repeated solves faster.

    ``max_workers`` defaults to ``min(4, cpu_count)`` and can be
    overridden via ``CONDA_PRESTO_WORKERS``.
    """
    global process_pool
    if process_pool is not None:
        return process_pool
    with pool_lock:
        if process_pool is None:
            process_pool = ProcessPoolExecutor(max_workers=MAX_WORKERS)
        return process_pool


def warmup_indexes(channels: list[str], platforms: list[str]):
    """Pre-build and cache ``RattlerIndexHelper`` for each platform.

    This does the expensive repodata fetch + parse once so that the
    first real solve request hits the cache and only pays SAT time.
    """
    ch = tuple(channels)
    with platform_lock:
        for platform in platforms:
            configure_platform(platform)
            configure_context()
            build_index(ch, platform)


def warmup(channels: list[str], platforms: list[str]):
    """Pre-warm index caches for both the parent process and worker pool.

    Builds cached indexes in the parent process first, then dispatches
    warmup tasks to each worker process so they build their own
    in-memory index caches.  This ensures the first real solve request
    doesn't pay the full cold-start cost.
    """
    warmup_indexes(channels, platforms)

    pool = get_process_pool()
    futures = [
        pool.submit(warmup_indexes, channels, [p]) for p in platforms
    ]
    for f in futures:
        f.result()
