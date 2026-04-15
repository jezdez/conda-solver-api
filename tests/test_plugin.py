"""Tests for conda_solver_api.plugin registration."""
from __future__ import annotations

import argparse

import pytest

from conda_solver_api.plugin import conda_subcommands


def test_plugin_yields_subcommand():
    subcommands = list(conda_subcommands())
    assert len(subcommands) == 1
    sc = subcommands[0]
    assert sc.name == "solver-api"
    assert sc.summary
    assert callable(sc.action)
    assert callable(sc.configure_parser)


def test_plugin_configure_parser_adds_subcommands():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["solve", "-c", "conda-forge", "-p", "linux-64", "zlib"]
    )
    assert args.subcommand == "solve"
    assert args.channel == ["conda-forge"]
    assert args.platforms == ["linux-64"]
    assert args.specs == ["zlib"]


@pytest.mark.parametrize(
    "argv, expected_subcommand",
    [
        (["solve", "zlib"], "solve"),
        (["serve"], "serve"),
    ],
)
def test_plugin_parser_recognizes_subcommands(
    argv, expected_subcommand
):
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(argv)
    assert args.subcommand == expected_subcommand


def test_plugin_parser_accepts_solver_flag():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["solve", "--solver", "rattler", "-c", "conda-forge", "zlib"]
    )
    assert args.solver == "rattler"


def test_plugin_parser_accepts_explicit_flag():
    sc = next(iter(conda_subcommands()))
    parser = argparse.ArgumentParser()
    sc.configure_parser(parser)
    args = parser.parse_args(
        ["solve", "--explicit", "--md5", "-c", "conda-forge", "zlib"]
    )
    assert args.explicit is True
    assert args.md5 is True
