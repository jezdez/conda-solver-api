"""Conda plugin registration for conda-solver-api.

This module is imported on every conda invocation via the entry point
system.  Only hookimpl and type imports are at module level — the CLI
is lazily imported inside the hook to keep startup overhead minimal.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from conda.plugins import hookimpl
from conda.plugins.types import CondaSubcommand

if TYPE_CHECKING:
    from collections.abc import Iterable


@hookimpl
def conda_subcommands() -> Iterable[CondaSubcommand]:
    from .cli import configure_parser, execute

    yield CondaSubcommand(
        name="solver-api",
        summary=(
            "Dry-run solver: resolve specs or environment.yml"
            " to pinned packages, or serve as an HTTP API."
        ),
        action=execute,
        configure_parser=configure_parser,
    )
