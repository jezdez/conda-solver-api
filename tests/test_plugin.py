"""Tests for conda_resolve.plugin registration."""
from __future__ import annotations

import argparse

from conda_resolve.plugin import conda_subcommands


def test_plugin_yields_subcommand():
    subcommands = list(conda_subcommands())
    assert len(subcommands) == 1
    sc = subcommands[0]
    assert sc.name == "resolve"
    assert sc.summary
    assert callable(sc.action)
    assert callable(sc.configure_parser)


def test_plugin_configure_parser():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["-c", "conda-forge", "-p", "linux-64", "zlib"]
    )
    assert args.channel == ["conda-forge"]
    assert args.platforms == ["linux-64"]
    assert args.specs == ["zlib"]
    assert args.serve is False


def test_plugin_parser_accepts_solver_flag():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["--solver", "rattler", "-c", "conda-forge", "zlib"]
    )
    assert args.solver == "rattler"


def test_plugin_parser_accepts_format_flag():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["--format", "yaml", "-c", "conda-forge", "zlib"]
    )
    assert args.output_format == "yaml"


def test_plugin_parser_default_format_is_json():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["-c", "conda-forge", "zlib"]
    )
    assert args.output_format == "resolve-json"


def test_plugin_parser_serve_flag():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(["--serve", "--port", "9000"])
    assert args.serve is True
    assert args.port == 9000
