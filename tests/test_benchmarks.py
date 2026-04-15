"""Benchmarks for conda_solver_api hot paths using pytest-benchmark."""
from __future__ import annotations

import pytest

from conda_solver_api.resolve import (
    ResolvedPackage,
    SolveRequest,
    SolveResult,
    solve,
    solve_one_platform,
)


def test_bench_from_record(benchmark, sample_record):
    benchmark(ResolvedPackage.from_record, sample_record)


def test_bench_from_record_batch(benchmark, many_records):
    def _convert_all():
        return [ResolvedPackage.from_record(r) for r in many_records]

    benchmark(_convert_all)


def test_bench_to_dict_single(benchmark, sample_resolved_package):
    benchmark(sample_resolved_package.to_dict)


def test_bench_to_dict_batch(benchmark, many_records):
    packages = [ResolvedPackage.from_record(r) for r in many_records]

    def _serialize_all():
        return [p.to_dict() for p in packages]

    benchmark(_serialize_all)


def test_bench_solve_result_to_dict(benchmark, many_records):
    packages = [ResolvedPackage.from_record(r) for r in many_records]
    result = SolveResult(platform="linux-64", packages=packages)
    benchmark(result.to_dict)


def test_bench_solve_one_platform_zlib(benchmark):
    benchmark(
        solve_one_platform,
        channels=("conda-forge",),
        dependencies=["zlib"],
        platform="linux-64",
    )


@pytest.mark.parametrize(
    "deps",
    [
        pytest.param(["zlib"], id="1-dep"),
        pytest.param(["python=3.12", "numpy"], id="2-deps"),
    ],
)
def test_bench_solve_varying_deps(benchmark, deps):
    req = SolveRequest(
        channels=["conda-forge"],
        dependencies=deps,
        platforms=["linux-64"],
    )
    benchmark(solve, req)


def test_bench_solve_multi_platform(benchmark):
    req = SolveRequest(
        channels=["conda-forge"],
        dependencies=["zlib"],
        platforms=["linux-64", "osx-arm64"],
    )
    benchmark(solve, req)
