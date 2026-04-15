"""Benchmarks for conda_resolve hot paths using pytest-benchmark.

Compares the server path (ResolvedPackage/SolveResult) against the
CLI path (PackageRecord/Environment) to verify the server types
provide measurable serialization and memory benefits.
"""
from __future__ import annotations

import json
import sys

import pytest
from conda.models.environment import Environment

from conda_resolve.resolve import (
    ResolvedPackage,
    SolveResult,
    solve,
)


@pytest.fixture()
def resolved_packages(many_records):
    return [ResolvedPackage.from_record(r) for r in many_records]


@pytest.fixture()
def solve_result(resolved_packages):
    return SolveResult(platform="linux-64", packages=resolved_packages)


def test_bench_from_record(benchmark, sample_record):
    benchmark(ResolvedPackage.from_record, sample_record)


def test_bench_from_record_batch(benchmark, many_records):
    benchmark(lambda: [ResolvedPackage.from_record(r) for r in many_records])


def test_bench_to_dict_single(benchmark, sample_resolved_package):
    benchmark(sample_resolved_package.to_dict)


def test_bench_to_dict_batch(benchmark, resolved_packages):
    benchmark(lambda: [p.to_dict() for p in resolved_packages])


def test_bench_solve_result_to_dict(benchmark, solve_result):
    benchmark(solve_result.to_dict)


@pytest.mark.parametrize(
    "deps",
    [
        pytest.param(["zlib"], id="1-dep"),
        pytest.param(["python=3.12", "numpy"], id="2-deps"),
    ],
)
def test_bench_solve(benchmark, deps):
    benchmark(solve, ["conda-forge"], deps, ["linux-64"])


@pytest.mark.crossplatform
def test_bench_solve_multi_platform(benchmark):
    benchmark(
        solve, ["conda-forge"], ["zlib"], ["linux-64", "osx-arm64"]
    )


def test_bench_server_path_serialize(benchmark, many_records):
    """Server path: PackageRecord -> ResolvedPackage -> to_dict -> JSON."""
    def run():
        packages = [ResolvedPackage.from_record(r) for r in many_records]
        result = SolveResult(platform="linux-64", packages=packages)
        return json.dumps(result.to_dict())

    benchmark(run)


def test_bench_cli_path_construct(benchmark, many_records):
    """CLI path: PackageRecord stays as-is in Environment."""
    benchmark(
        lambda: Environment(
            platform="linux-64", explicit_packages=list(many_records)
        )
    )


def test_bench_memory_shallow_sizes(many_records, resolved_packages, solve_result):
    """Report shallow sizes (sys.getsizeof) for both paths.

    sys.getsizeof is unreliable for auxlib Entity objects —
    it reports only the container size, not internal storage.
    This test is informational only.
    """
    env = Environment(
        platform="linux-64", explicit_packages=list(many_records)
    )

    server_size = sys.getsizeof(solve_result) + sum(
        sys.getsizeof(p) for p in resolved_packages
    )
    cli_size = sys.getsizeof(env) + sum(
        sys.getsizeof(r) for r in many_records
    )

    print(f"\n  Server (SolveResult+ResolvedPackage): {server_size:,} bytes")
    print(f"  CLI (Environment+PackageRecord):      {cli_size:,} bytes")
    print("  Note: sys.getsizeof underreports auxlib Entity sizes")
