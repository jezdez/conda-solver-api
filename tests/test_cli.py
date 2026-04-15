"""Tests for conda_resolve.cli."""
from __future__ import annotations

import json

import pytest

from conda_resolve.cli import main


def _package_names(out: str) -> list[str]:
    """Extract package names from the default resolve-json output."""
    data = json.loads(out)
    assert isinstance(data, dict)
    return [p["name"] for p in data["packages"]]


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
    assert data["platform"] == "linux-64"
    assert "python" in _package_names(out)


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
    assert data["platform"] == "linux-64"
    assert "zlib" in _package_names(out)
    pkg = data["packages"][0]
    assert "sha256" in pkg
    assert "url" in pkg
    assert "channel" in pkg


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
    assert data["packages"]


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
    names = _package_names(out)
    assert "zlib" in names
    assert "bzip2" in names
