"""Tests for conda_resolve.resolve."""
from __future__ import annotations

import threading
from concurrent.futures import ProcessPoolExecutor

import pytest
from conda.models.environment import Environment
from conda.models.records import PackageRecord

from conda_resolve.resolve import (
    ResolvedPackage,
    SolveResult,
    build_index,
    clear_index_cache,
    configure_context,
    configure_platform,
    get_process_pool,
    index_cache,
    platform_lock,
    run_solver,
    solve,
    solve_environments,
    solve_one_platform,
)


@pytest.fixture()
def _warm_index():
    """Prepare a clean index cache with platform/context configured."""
    clear_index_cache()
    with platform_lock:
        configure_platform("linux-64")
        configure_context()
        yield
    clear_index_cache()


def test_resolved_package_from_record(sample_record):
    pkg = ResolvedPackage.from_record(sample_record)
    assert pkg.name == "zlib"
    assert pkg.version == "1.3.1"
    assert pkg.build == "h68df207_2"
    assert pkg.build_number == 2
    assert pkg.sha256 == "abc123"
    assert pkg.md5 == "def456"
    assert pkg.size == 102400
    assert isinstance(pkg.depends, tuple)
    assert isinstance(pkg.constrains, tuple)


def test_resolved_package_from_record_empty_optionals(sample_channel):
    record = PackageRecord(
        name="minimal",
        version="1.0",
        build="h0",
        build_number=0,
        channel=sample_channel,
        subdir="linux-64",
        fn="minimal-1.0-h0.conda",
        depends=(),
        constrains=(),
    )
    pkg = ResolvedPackage.from_record(record)
    assert pkg.sha256 == ""
    assert pkg.md5 == ""
    assert pkg.size is None
    assert pkg.depends == ()
    assert pkg.constrains == ()


def test_resolved_package_to_dict(sample_resolved_package):
    d = sample_resolved_package.to_dict()
    assert d["name"] == "zlib"
    assert d["version"] == "1.3.1"
    assert isinstance(d["depends"], list)
    assert isinstance(d["constrains"], list)
    assert set(d.keys()) == {
        "name",
        "version",
        "build",
        "build_number",
        "channel",
        "subdir",
        "url",
        "sha256",
        "md5",
        "size",
        "depends",
        "constrains",
    }


@pytest.mark.parametrize(
    "depends_in, depends_out",
    [
        (("a >=1", "b"), ["a >=1", "b"]),
        ((), []),
    ],
)
def test_resolved_package_to_dict_depends_types(depends_in, depends_out):
    pkg = ResolvedPackage(
        name="x",
        version="1",
        build="h0",
        build_number=0,
        channel="",
        subdir="",
        url="",
        sha256="",
        md5="",
        size=None,
        depends=depends_in,
        constrains=(),
    )
    assert pkg.to_dict()["depends"] == depends_out


@pytest.mark.parametrize(
    "packages, error, expected_error, expected_count",
    [
        pytest.param(
            None, None, None, 1,
            id="success",
        ),
        pytest.param(
            [], "solver failed", "solver failed", 0,
            id="error",
        ),
    ],
)
def test_solve_result_to_dict(
    packages, error, expected_error, expected_count, sample_resolved_package
):
    pkgs = [sample_resolved_package] if packages is None else packages
    result = SolveResult(platform="linux-64", packages=pkgs, error=error)
    d = result.to_dict()
    assert d["error"] == expected_error
    assert len(d["packages"]) == expected_count


def test_configure_context_sets_json():
    from conda.base.context import context

    configure_context()
    assert context.json is True


def test_get_process_pool():
    pool1 = get_process_pool()
    pool2 = get_process_pool()
    assert isinstance(pool1, ProcessPoolExecutor)
    assert pool1 is pool2


def test_get_process_pool_threadsafe():
    pools = []

    def grab():
        pools.append(get_process_pool())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(p is pools[0] for p in pools)


def test_run_solver_returns_sorted_records():
    records = run_solver(
        channels=("conda-forge",),
        dependencies=["zlib"],
        platform="linux-64",
    )
    assert all(isinstance(r, PackageRecord) for r in records)
    names = [r.name for r in records]
    assert "zlib" in names
    assert names == sorted(names)


def test_run_solver_unsatisfiable():
    from conda.exceptions import PackagesNotFoundError, UnsatisfiableError

    with pytest.raises((UnsatisfiableError, PackagesNotFoundError)):
        run_solver(
            channels=("conda-forge",),
            dependencies=["__nonexistent_package_xyz__"],
            platform="linux-64",
        )


@pytest.mark.parametrize(
    "deps",
    [
        pytest.param(["zlib"], id="single-dep"),
        pytest.param(["python=3.12", "zlib"], id="multi-dep"),
    ],
)
def test_solve_one_platform(deps):
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=deps,
        platform="linux-64",
    )
    assert isinstance(result, SolveResult)
    assert result.platform == "linux-64"
    assert result.error is None
    names = [p.name for p in result.packages]
    assert names == sorted(names)
    for dep in deps:
        name = dep.split("=")[0].split(">")[0].split("<")[0].strip()
        assert name in names
    for pkg in result.packages:
        assert pkg.sha256, f"{pkg.name} missing sha256"
        assert pkg.url, f"{pkg.name} missing url"


def test_solve_one_platform_unsatisfiable():
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=["__nonexistent_package_xyz__"],
        platform="linux-64",
    )
    assert result.error is not None
    assert result.packages == []


@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(["linux-64", "osx-arm64"], id="multi"),
    ],
)
def test_solve(platforms):
    results = solve(["conda-forge"], ["zlib"], platforms)
    assert len(results) == len(platforms)
    for result, platform in zip(results, platforms):
        assert result.platform == platform
        assert result.error is None
        assert len(result.packages) > 0


def test_solve_defaults_to_current_platform():
    from conda.base.context import context

    results = solve(["conda-forge"], ["zlib"])
    assert len(results) == 1
    assert results[0].platform == context.subdir


@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(["linux-64", "osx-arm64"], id="multi"),
    ],
)
def test_solve_environments(platforms):
    envs = solve_environments(["conda-forge"], ["zlib"], platforms)
    assert len(envs) == len(platforms)
    for env, platform in zip(envs, platforms):
        assert isinstance(env, Environment)
        assert env.platform == platform
        names = [r.name for r in env.explicit_packages]
        assert "zlib" in names


def test_solve_environments_defaults_to_current():
    from conda.base.context import context

    envs = solve_environments(["conda-forge"], ["zlib"])
    assert len(envs) == 1
    assert envs[0].platform == context.subdir


@pytest.mark.usefixtures("_warm_index")
def test_build_index_returns_same_object():
    """Calling build_index twice returns the same cached instance."""
    idx1 = build_index(("conda-forge",), "linux-64")
    idx2 = build_index(("conda-forge",), "linux-64")
    assert idx1 is idx2


@pytest.mark.usefixtures("_warm_index")
def test_build_index_different_platforms():
    """Different platforms get separate cache entries."""
    idx_linux = build_index(("conda-forge",), "linux-64")
    configure_platform("osx-arm64")
    idx_osx = build_index(("conda-forge",), "osx-arm64")
    assert idx_linux is not idx_osx
    assert len(index_cache) >= 2


@pytest.mark.usefixtures("_warm_index")
def test_clear_index_cache():
    """clear_index_cache() forces a fresh index on next call."""
    idx1 = build_index(("conda-forge",), "linux-64")
    clear_index_cache()
    assert len(index_cache) == 0
    idx2 = build_index(("conda-forge",), "linux-64")
    assert idx2 is not idx1


def test_cached_solve_produces_correct_results():
    """Two back-to-back solves produce identical results (cache hit)."""
    clear_index_cache()
    r1 = solve(["conda-forge"], ["zlib"], ["linux-64"])
    r2 = solve(["conda-forge"], ["zlib"], ["linux-64"])
    names1 = [p.name for p in r1[0].packages]
    names2 = [p.name for p in r2[0].packages]
    assert names1 == names2
