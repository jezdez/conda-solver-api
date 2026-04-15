"""Tests for conda_resolve.cli."""
from __future__ import annotations

import json

import pytest

from conda_resolve.cli import main


@pytest.fixture()
def run_cli(capsys, monkeypatch):
    """Return a helper that invokes the CLI with the given argv list."""

    def _run(*argv: str) -> str:
        monkeypatch.setattr("sys.argv", ["conda-resolve", *argv])
        main()
        return capsys.readouterr().out

    return _run


def test_solve_with_file(run_cli, environment_yml_path):
    out = run_cli("-f", str(environment_yml_path), "-p", "linux-64")
    data = json.loads(out)
    assert data["platform"] == "linux-64"
    assert "python" in [p["name"] for p in data["packages"]]


def test_solve_with_inline_specs(run_cli):
    out = run_cli("-c", "conda-forge", "-p", "linux-64", "zlib")
    data = json.loads(out)
    assert data["platform"] == "linux-64"
    names = [p["name"] for p in data["packages"]]
    assert "zlib" in names
    pkg = data["packages"][0]
    assert "sha256" in pkg
    assert "url" in pkg
    assert "channel" in pkg


def test_solve_file_channels_override(run_cli, environment_yml_path):
    out = run_cli(
        "-f", str(environment_yml_path), "-c", "conda-forge", "-p", "linux-64"
    )
    data = json.loads(out)
    assert data["packages"]


def test_solve_no_args_exits(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["conda-resolve"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "Provide an environment file" in err


@pytest.mark.parametrize(
    "fmt, assertion",
    [
        pytest.param("explicit", "@EXPLICIT", id="explicit"),
        pytest.param("yaml", "dependencies:", id="yaml"),
    ],
)
def test_solve_output_format(run_cli, fmt, assertion):
    out = run_cli(
        "-c", "conda-forge", "-p", "linux-64", "--format", fmt, "zlib"
    )
    assert assertion in out


def test_solve_multiple_files(run_cli, tmp_path):
    file1 = tmp_path / "env1.yml"
    file1.write_text(
        "name: a\nchannels:\n  - conda-forge\n"
        "dependencies:\n  - zlib\n"
    )
    file2 = tmp_path / "env2.yml"
    file2.write_text(
        "name: b\nchannels:\n  - conda-forge\n"
        "dependencies:\n  - bzip2\n"
    )
    out = run_cli(
        "-f", str(file1), "-f", str(file2), "-p", "linux-64"
    )
    data = json.loads(out)
    names = [p["name"] for p in data["packages"]]
    assert "zlib" in names
    assert "bzip2" in names
