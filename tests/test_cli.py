"""Tests for conda_solver_api.cli."""
from __future__ import annotations

import json

import pytest

from conda_solver_api.cli import main


def test_solve_with_file(
    capsys, monkeypatch, environment_yml_path
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-f",
            str(environment_yml_path),
            "-p",
            "linux-64",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["platform"] == "linux-64"
    names = [p["name"] for p in data[0]["packages"]]
    assert "python" in names


def test_solve_with_inline_specs(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    names = [p["name"] for p in data[0]["packages"]]
    assert "zlib" in names


def test_solve_file_channels_override(
    capsys, monkeypatch, environment_yml_path
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-f",
            str(environment_yml_path),
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data[0]["error"] is None


def test_solve_no_args_exits(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["conda-solver-api", "solve"]
    )
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "Provide an environment.yml" in err


def test_no_subcommand_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["conda-solver-api"])
    with pytest.raises(SystemExit, match="1"):
        main()


def test_solve_output_is_valid_json(
    capsys, monkeypatch
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert isinstance(data[0], dict)
    assert "platform" in data[0]
    assert "packages" in data[0]


@pytest.mark.parametrize(
    "extra_args",
    [
        ["-p", "linux-64"],
        ["-p", "linux-64", "-p", "osx-arm64"],
    ],
)
def test_solve_platform_flag(
    capsys, monkeypatch, extra_args
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            *extra_args,
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == len(extra_args) // 2


def test_solve_packages_have_sha256(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    for pkg in data[0]["packages"]:
        assert pkg["sha256"], f"{pkg['name']} missing sha256"
