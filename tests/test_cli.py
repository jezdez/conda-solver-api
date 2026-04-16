"""Tests for conda_presto.cli."""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from conda_presto.cli import (
    _export_environments,
    _load_files,
    cmd_serve,
    execute,
    main,
)


@pytest.fixture()
def run_cli(capsys, monkeypatch):
    """Return a helper that invokes the CLI with the given argv list."""

    def _run(*argv: str) -> str:
        monkeypatch.setattr("sys.argv", ["conda-presto", *argv])
        main()
        return capsys.readouterr().out

    return _run


@pytest.mark.parametrize(
    "extra_args, expected_name",
    [
        pytest.param([], "python", id="file-only"),
        pytest.param(["-c", "conda-forge"], "python", id="file-with-channel"),
    ],
)
def test_solve_with_file(
    run_cli, environment_yml_path, extra_args, expected_name
):
    out = run_cli(
        "-f", str(environment_yml_path), "-p", "linux-64", *extra_args
    )
    data = json.loads(out)
    assert data["platform"] == "linux-64"
    assert expected_name in [p["name"] for p in data["packages"]]


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


def test_solve_no_args_exits(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["conda-presto"])
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


def test_load_files_unhandled(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "env.yml"
    bad.write_text("name: test\nchannels:\n  - conda-forge\ndependencies:\n  - zlib\n")

    class FakeSpec:
        def can_handle(self):
            return False

    class FakePlugin:
        def environment_spec(self, filename):
            return FakeSpec()

    monkeypatch.setattr(
        "conda_presto.cli.context.plugin_manager.detect_environment_specifier",
        lambda fpath: FakePlugin(),
    )
    with pytest.raises(SystemExit, match="1"):
        _load_files([str(bad)])
    assert "No environment spec plugin can handle" in capsys.readouterr().err


def test_execute_serve_branch(monkeypatch):
    called = []
    monkeypatch.setattr(
        "conda_presto.cli.cmd_serve",
        lambda args: called.append(args),
    )
    args = argparse.Namespace(serve=True, host="127.0.0.1", port=8000)
    execute(args)
    assert len(called) == 1


def test_cmd_serve(monkeypatch):
    called = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, host, port: called.append((app, host, port)),
    )
    args = argparse.Namespace(host="0.0.0.0", port=9000)
    cmd_serve(args)
    assert called == [("conda_presto.app:app", "0.0.0.0", 9000)]


@pytest.mark.parametrize(
    "exporter_attrs, envs, expected_result, exits",
    [
        pytest.param(
            {"multiplatform_export": lambda e: f"multi:{len(e)}"},
            ["env1", "env2"],
            "multi:2",
            False,
            id="multiplatform",
        ),
        pytest.param(
            {"multiplatform_export": None, "export": None},
            ["env1"],
            None,
            True,
            id="no-method",
        ),
    ],
)
def test_export_environments_edge_cases(
    monkeypatch, capsys, exporter_attrs, envs, expected_result, exits
):
    attrs = {"multiplatform_export": None, "export": None, **exporter_attrs}
    fake_exporter = SimpleNamespace(**attrs)
    monkeypatch.setattr(
        "conda_presto.cli.context.plugin_manager"
        ".get_environment_exporter_by_format",
        lambda fmt: fake_exporter,
    )
    if exits:
        with pytest.raises(SystemExit, match="1"):
            _export_environments(envs, "test-fmt")
        assert "No export method" in capsys.readouterr().err
    else:
        assert _export_environments(envs, "test-fmt") == expected_result
