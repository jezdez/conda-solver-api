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
    _run_solver,
    configure_context,
    get_process_pool,
    solve,
    solve_environments,
    solve_one_platform,
)


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
def test_resolved_package_to_dict_depends_types(
    depends_in, depends_out
):
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


def test_solve_result_to_dict(sample_solve_result):
    d = sample_solve_result.to_dict()
    assert d["platform"] == "linux-64"
    assert len(d["packages"]) == 1
    assert d["packages"][0]["name"] == "zlib"
    assert d["error"] is None


def test_solve_result_error_to_dict():
    result = SolveResult(
        platform="linux-64", packages=[], error="solver failed"
    )
    d = result.to_dict()
    assert d["error"] == "solver failed"
    assert d["packages"] == []


def test_configure_context_sets_json():
    from conda.base.context import context

    configure_context()
    assert context.json is True


def test_get_process_pool_returns_executor():
    pool = get_process_pool()
    assert isinstance(pool, ProcessPoolExecutor)


def test_get_process_pool_is_singleton():
    pool1 = get_process_pool()
    pool2 = get_process_pool()
    assert pool1 is pool2


def test_get_process_pool_threadsafe():
    pools = []

    def _grab():
        pools.append(get_process_pool())

    threads = [threading.Thread(target=_grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(p is pools[0] for p in pools)


def test_run_solver_returns_records():
    records = _run_solver(
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
        _run_solver(
            channels=("conda-forge",),
            dependencies=["__nonexistent_package_xyz__"],
            platform="linux-64",
        )


def test_solve_one_platform_returns_result():
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=["zlib"],
        platform="linux-64",
    )
    assert isinstance(result, SolveResult)
    assert result.platform == "linux-64"
    assert result.error is None
    assert len(result.packages) > 0
    names = [p.name for p in result.packages]
    assert "zlib" in names


def test_solve_one_platform_packages_sorted():
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=["zlib"],
        platform="linux-64",
    )
    names = [p.name for p in result.packages]
    assert names == sorted(names)


def test_solve_one_platform_has_hashes():
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=["zlib"],
        platform="linux-64",
    )
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


def test_solve_single_platform():
    results = solve(["conda-forge"], ["zlib"], ["linux-64"])
    assert len(results) == 1
    assert results[0].platform == "linux-64"
    assert results[0].error is None


def test_solve_multi_platform():
    results = solve(
        ["conda-forge"], ["zlib"], ["linux-64", "osx-arm64"]
    )
    assert len(results) == 2
    platforms = [r.platform for r in results]
    assert platforms == ["linux-64", "osx-arm64"]
    for r in results:
        assert r.error is None
        assert len(r.packages) > 0


def test_solve_defaults_to_current_platform():
    from conda.base.context import context

    results = solve(["conda-forge"], ["zlib"])
    assert len(results) == 1
    assert results[0].platform == context.subdir


def test_solve_environments_single():
    envs = solve_environments(
        ["conda-forge"], ["zlib"], ["linux-64"]
    )
    assert len(envs) == 1
    assert isinstance(envs[0], Environment)
    assert envs[0].platform == "linux-64"
    names = [r.name for r in envs[0].explicit_packages]
    assert "zlib" in names


def test_solve_environments_multi():
    envs = solve_environments(
        ["conda-forge"], ["zlib"], ["linux-64", "osx-arm64"]
    )
    assert len(envs) == 2
    platforms = [e.platform for e in envs]
    assert platforms == ["linux-64", "osx-arm64"]


def test_solve_environments_defaults_to_current():
    from conda.base.context import context

    envs = solve_environments(["conda-forge"], ["zlib"])
    assert len(envs) == 1
    assert envs[0].platform == context.subdir


@pytest.mark.parametrize(
    "deps",
    [
        ["zlib"],
        ["python=3.12", "zlib"],
    ],
)
def test_solve_varying_deps(deps):
    results = solve(["conda-forge"], deps, ["linux-64"])
    assert results[0].error is None
    resolved_names = {p.name for p in results[0].packages}
    for dep in deps:
        name = dep.split("=")[0].split(">")[0].split("<")[0].strip()
        assert name in resolved_names
