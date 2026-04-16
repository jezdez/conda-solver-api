"""Conda plugin registration for conda-presto.

This module is imported on every ``conda`` invocation via the entry
point system.  Only ``hookimpl`` and type imports are at module level;
the CLI module is lazily imported inside the hook to keep the startup
overhead under 1 ms (conda loads all registered plugins on every
command, including ``conda activate`` and ``conda --version``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from conda.plugins import hookimpl
from conda.plugins.types import CondaEnvironmentExporter, CondaSubcommand

if TYPE_CHECKING:
    from collections.abc import Iterable


@hookimpl
def conda_subcommands() -> Iterable[CondaSubcommand]:
    """Register ``conda presto`` as a conda subcommand."""
    from .cli import configure_parser, execute

    yield CondaSubcommand(
        name="presto",
        summary=(
            "Dry-run solver: resolve specs or environment.yml"
            " to pinned packages, or serve as an HTTP API."
        ),
        action=execute,
        configure_parser=configure_parser,
    )


@hookimpl
def conda_environment_exporters() -> Iterable[CondaEnvironmentExporter]:
    """Register the ``resolve-json`` exporter format."""
    from .exporter import export_resolve_json

    yield CondaEnvironmentExporter(
        name="resolve-json",
        aliases=(),
        default_filenames=(),
        export=export_resolve_json,
        description="Full package metadata with sha256, urls, sizes",
    )
