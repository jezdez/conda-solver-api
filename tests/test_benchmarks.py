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
    benchmark(solve, ["conda-forge"], deps, ["linux-64"])


def test_bench_solve_multi_platform(benchmark):
    benchmark(
        solve, ["conda-forge"], ["zlib"], ["linux-64", "osx-arm64"]
    )


# -------------------------------------------------------------------
# Server vs CLI path comparison benchmarks
# -------------------------------------------------------------------


def test_bench_server_path_serialize(benchmark, many_records):
    """Server path: PackageRecord -> ResolvedPackage -> to_dict -> JSON."""
    def _server_path():
        packages = [
            ResolvedPackage.from_record(r) for r in many_records
        ]
        result = SolveResult(
            platform="linux-64", packages=packages
        )
        return json.dumps(result.to_dict())

    benchmark(_server_path)


def test_bench_cli_path_construct(benchmark, many_records):
    """CLI path: PackageRecord stays as-is in Environment."""
    def _cli_path():
        return Environment(
            platform="linux-64",
            explicit_packages=list(many_records),
        )

    benchmark(_cli_path)


def test_bench_memory_shallow_sizes(many_records):
    """Report shallow sizes (sys.getsizeof) for both paths.

    Note: sys.getsizeof is unreliable for auxlib Entity objects —
    it reports only the container size, not internal storage.
    This test is informational only.
    """
    packages = [
        ResolvedPackage.from_record(r) for r in many_records
    ]
    result = SolveResult(platform="linux-64", packages=packages)

    env = Environment(
        platform="linux-64", explicit_packages=list(many_records)
    )

    server_size = sys.getsizeof(result) + sum(
        sys.getsizeof(p) for p in packages
    )
    cli_size = sys.getsizeof(env) + sum(
        sys.getsizeof(r) for r in many_records
    )

    print(f"\n  Server (SolveResult+ResolvedPackage): {server_size:,} bytes")
    print(f"  CLI (Environment+PackageRecord):      {cli_size:,} bytes")
    print(
        "  Note: sys.getsizeof underreports auxlib Entity sizes"
    )
