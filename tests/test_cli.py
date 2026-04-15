"""Tests for conda_solver_api.cli."""
from __future__ import annotations

import json

import pytest

from conda_solver_api.cli import main


def test_solve_with_file(capsys, monkeypatch, environment_yml_path):
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


def test_solve_output_is_valid_json(capsys, monkeypatch):
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
def test_solve_platform_flag(capsys, monkeypatch, extra_args):
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


def test_solve_explicit_output(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--explicit",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "@EXPLICIT" in out
    assert "https://" in out
    assert "#" not in out.split("@EXPLICIT")[1].split("\n")[1]


def test_solve_explicit_with_md5(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--explicit",
            "--md5",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "@EXPLICIT" in out
    url_lines = [
        line
        for line in out.split("@EXPLICIT")[1].strip().splitlines()
        if line and not line.startswith("#")
    ]
    assert all("#" in line for line in url_lines)


def test_solve_no_builds_output(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--no-builds",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    for line in out.strip().splitlines():
        assert "==" in line
        parts = line.split("==")
        assert "=" not in parts[-1]


def test_solve_no_channels_output(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--no-channels",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    for line in out.strip().splitlines():
        assert "::" not in line


def test_solve_multiple_files(
    capsys, monkeypatch, tmp_path
):
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
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-solver-api",
            "solve",
            "-f",
            str(file1),
            "-f",
            str(file2),
            "-p",
            "linux-64",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    names = [p["name"] for p in data[0]["packages"]]
    assert "zlib" in names
    assert "bzip2" in names
