"""Tests for conda_presto.exporter."""
from __future__ import annotations

import json

import pytest
from conda.models.environment import Environment

from conda_presto.exporter import _record_to_dict, export_resolve_json


@pytest.fixture()
def env_with_records(make_package_record):
    records = [
        make_package_record(name="zlib", version="1.3.1"),
        make_package_record(name="bzip2", version="1.0.8", build="h0"),
    ]
    return Environment(platform="linux-64", explicit_packages=records)


@pytest.mark.parametrize(
    "record_fixture, field, expected",
    [
        pytest.param("sample_record", "name", "zlib", id="name"),
        pytest.param("sample_record", "version", "1.3.1", id="version"),
        pytest.param("sample_record", "build", "h68df207_2", id="build"),
        pytest.param("sample_record", "build_number", 2, id="build_number"),
        pytest.param("sample_record", "sha256", "abc123", id="sha256"),
        pytest.param("sample_record", "md5", "def456", id="md5"),
        pytest.param("sample_record", "size", 102400, id="size"),
        pytest.param(
            "sample_record", "depends", ["libgcc-ng >=12"], id="depends"
        ),
        pytest.param("sample_record", "constrains", [], id="constrains"),
        pytest.param("minimal_record", "sha256", "", id="empty-sha256"),
        pytest.param("minimal_record", "md5", "", id="empty-md5"),
        pytest.param("minimal_record", "depends", [], id="empty-depends"),
        pytest.param(
            "minimal_record", "constrains", [], id="empty-constrains"
        ),
    ],
)
def test_record_to_dict(record_fixture, field, expected, request):
    record = request.getfixturevalue(record_fixture)
    assert _record_to_dict(record)[field] == expected


def test_export_resolve_json(env_with_records):
    output = export_resolve_json(env_with_records)
    data = json.loads(output)
    assert data["platform"] == "linux-64"
    names = [p["name"] for p in data["packages"]]
    assert names == sorted(names)
    assert len(data["packages"]) == 2
    pkg = data["packages"][1]
    assert pkg["sha256"] == "abc123"
    assert pkg["url"]
