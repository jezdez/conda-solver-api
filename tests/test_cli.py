"""Tests for conda_resolve.cli."""
from __future__ import annotations

import json

import pytest

from conda_resolve.cli import main


def test_solve_with_file(capsys, monkeypatch, environment_yml_path):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
            "-f",
            str(environment_yml_path),
            "-p",
            "linux-64",
        ],
    )
    main()
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "dependencies" in data
    deps = data["dependencies"]
    names = [d.split("=")[0] for d in deps]
    assert "python" in names


def test_solve_with_inline_specs(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
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
    assert isinstance(data, dict)
    assert "dependencies" in data
    deps = data["dependencies"]
    names = [d.split("=")[0] for d in deps]
    assert "zlib" in names


def test_solve_file_channels_override(
    capsys, monkeypatch, environment_yml_path
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
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
    assert "dependencies" in data


def test_solve_no_args_exits(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["conda-resolve"]
    )
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "Provide an environment file" in err


def test_solve_multi_platform_yaml(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "-p",
            "osx-arm64",
            "--format",
            "yaml",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "dependencies:" in out


def test_solve_explicit_output(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
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


def test_solve_format_explicit(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--format",
            "explicit",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "@EXPLICIT" in out


def test_solve_format_yaml(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "conda-resolve",
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--format",
            "yaml",
            "zlib",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "dependencies:" in out


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
            "conda-resolve",
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
    deps = data["dependencies"]
    names = [d.split("=")[0] for d in deps]
    assert "zlib" in names
    assert "bzip2" in names
