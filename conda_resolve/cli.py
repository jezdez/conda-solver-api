"""CLI for conda-resolve.

Exposes ``configure_parser`` and ``execute`` for the conda plugin hook
(``conda resolve ...``), and ``main`` for standalone use via the
``conda-resolve`` script entry point.

The CLI uses conda's ``Environment`` model and exporter plugins
end-to-end, avoiding intermediate types.  The default output format
is ``environment-json``.

Resolve is the default action.  Use ``--serve`` to start the HTTP API
server instead.
"""
from __future__ import annotations

import argparse
import sys

from conda.base.context import context
from conda.cli.helpers import (
    add_parser_channels,
    add_parser_networking,
    add_parser_solver,
)
from conda.models.environment import Environment

from .resolve import solve_environments

ENVIRONMENT_JSON_FORMAT = "environment-json"


def configure_parser(parser: argparse.ArgumentParser):
    """Add resolve arguments and the ``--serve`` flag to *parser*.

    Used by both the conda plugin hook and the standalone ``main()``.
    """
    add_parser_channels(parser)
    add_parser_networking(parser)
    add_parser_solver(parser)

    parser.add_argument(
        "-p",
        "--platform",
        action="append",
        default=[],
        dest="platforms",
        metavar="SUBDIR",
        help="Target platform (e.g. linux-64, osx-arm64). "
        "May be specified multiple times for parallel solves.",
    )

    parser.add_argument(
        "-f",
        "--file",
        action="append",
        default=[],
        dest="files",
        help="Read package specs from file (any format supported by "
        "conda's env spec plugins: environment.yml, requirements.txt, "
        "explicit lockfiles, etc.). May be specified multiple times.",
    )

    output_group = parser.add_argument_group("Output Format")
    output_group.add_argument(
        "--format",
        default=ENVIRONMENT_JSON_FORMAT,
        dest="output_format",
        metavar="FORMAT",
        help="Output format using conda's export plugins "
        "(e.g. explicit, yaml, json, requirements). "
        "Default: environment-json.",
    )
    output_group.add_argument(
        "--explicit",
        action="store_const",
        const="explicit",
        dest="output_format",
        help="Shorthand for --format explicit.",
    )

    server_group = parser.add_argument_group("HTTP Server")
    server_group.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start the HTTP API server instead of resolving.",
    )
    server_group.add_argument(
        "--host", default="127.0.0.1", help="Server bind address."
    )
    server_group.add_argument(
        "--port", type=int, default=8000, help="Server port."
    )

    parser.add_argument(
        "specs", nargs="*", help="Inline package specs"
    )


def execute(args: argparse.Namespace):
    """Dispatch based on ``--serve`` flag (conda plugin action)."""
    if args.serve:
        cmd_serve(args)
    else:
        cmd_solve(args)


def _load_files(
    files: list[str],
) -> tuple[list[str], list[str]]:
    """Parse input files using conda's env spec plugin system.

    Returns accumulated *(dependencies, channels)* from all files.
    Each file is detected by conda's plugin registry and parsed into
    a conda ``Environment`` model.
    """
    deps: list[str] = []
    channels: list[str] = []
    for fpath in files:
        spec_plugin = context.plugin_manager.detect_environment_specifier(fpath)
        spec = spec_plugin.environment_spec(filename=fpath)
        if not spec.can_handle():
            print(
                f"No environment spec plugin can handle: {fpath}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        env = spec.env
        deps.extend(str(s) for s in env.requested_packages)
        if env.config and env.config.channels:
            channels.extend(env.config.channels)
    return deps, channels


def _export_environments(
    envs: list[Environment], format_name: str
) -> str:
    """Format environments using conda's exporter plugins."""
    exporter = context.plugin_manager.get_environment_exporter_by_format(
        format_name
    )

    if exporter.multiplatform_export and len(envs) > 1:
        return exporter.multiplatform_export(envs)
    elif exporter.export:
        return "\n".join(exporter.export(env) for env in envs)
    else:
        print(
            f"No export method for format: {format_name}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def cmd_solve(args: argparse.Namespace):
    """Resolve packages and write output to stdout."""
    context.__init__(argparse_args=args)

    file_deps, file_channels = _load_files(args.files)

    specs = [s.strip("\"'") for s in args.specs]
    deps = file_deps + specs

    if not deps:
        print(
            "Provide an environment file (--file) or package specs.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    channels = list(context.channels)
    if not channels or channels == ["defaults"]:
        channels = file_channels or channels

    envs = solve_environments(channels, deps, args.platforms or None)

    content = _export_environments(envs, args.output_format)
    sys.stdout.write(content.rstrip() + "\n")


def cmd_serve(args: argparse.Namespace):
    """Start the HTTP API server via uvicorn."""
    import uvicorn

    uvicorn.run(
        "conda_resolve.app:app", host=args.host, port=args.port
    )


def main():
    """Standalone entry point for the ``conda-resolve`` script."""
    parser = argparse.ArgumentParser(
        description="Resolve conda environments to fully pinned "
        "packages with SHA256 hashes.",
    )
    configure_parser(parser)
    args = parser.parse_args()
    execute(args)


if __name__ == "__main__":
    main()
