"""CLI for conda-solver-api.

Exposes ``configure_parser`` and ``execute`` for the conda plugin hook
(``conda solver-api ...``), and ``main`` for standalone use via the
``conda-solver-api`` script entry point.

The CLI has two subcommands:

- ``solve`` — resolve an environment.yml or inline specs
- ``serve`` — start the HTTP API server (uvicorn)
"""
from __future__ import annotations

import argparse
import json
import sys

import uvicorn
from conda.base.context import context
from conda.cli.helpers import (
    add_parser_channels,
    add_parser_networking,
    add_parser_solver,
)

from .resolve import SolveRequest, SolveResult, solve


def _add_solve_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Build the ``solve`` subcommand parser with conda-native flags."""
    solve_p = sub.add_parser(
        "solve", help="Solve an environment.yml or inline specs"
    )

    add_parser_channels(solve_p)
    add_parser_networking(solve_p)
    add_parser_solver(solve_p)

    solve_p.add_argument(
        "-p",
        "--platform",
        action="append",
        default=[],
        dest="platforms",
        metavar="SUBDIR",
        help="Target platform (e.g. linux-64, osx-arm64). "
        "May be specified multiple times for parallel solves.",
    )

    solve_p.add_argument(
        "-f",
        "--file",
        action="append",
        default=[],
        dest="files",
        help="Read package specs from file (environment.yml or "
        "plain text). May be specified multiple times.",
    )

    output_group = solve_p.add_argument_group("Output Format")
    output_group.add_argument(
        "--explicit",
        action="store_true",
        default=False,
        help="Output an explicit lockfile (URLs, one per line).",
    )
    output_group.add_argument(
        "--md5",
        action="store_true",
        default=False,
        help="Append #md5 hash to URLs in explicit output.",
    )
    output_group.add_argument(
        "--no-builds",
        action="store_true",
        default=False,
        help="Omit build strings from text output.",
    )
    output_group.add_argument(
        "--no-channels",
        action="store_true",
        default=False,
        help="Omit channel prefix from text output.",
    )

    solve_p.add_argument(
        "specs", nargs="*", help="Inline package specs"
    )

    return solve_p


def configure_parser(parser: argparse.ArgumentParser):
    """Add ``solve`` and ``serve`` subcommands to *parser*.

    Used by both the conda plugin hook and the standalone ``main()``.
    """
    sub = parser.add_subparsers(dest="subcommand")
    _add_solve_parser(sub)

    serve_p = sub.add_parser("serve", help="Run the HTTP API server")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)


def execute(args: argparse.Namespace):
    """Dispatch to the chosen subcommand (conda plugin action)."""
    if args.subcommand == "solve":
        cmd_solve(args)
    elif args.subcommand == "serve":
        cmd_serve(args)
    else:
        print(
            "Usage: conda solver-api {solve,serve} [options]",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _format_explicit(results: list[SolveResult], *, md5: bool) -> str:
    """Format results as an explicit lockfile."""
    lines: list[str] = []
    for result in results:
        if result.error:
            lines.append(f"# Error ({result.platform}): {result.error}")
            continue
        lines.append(
            "# This file may be used to create an environment using:"
        )
        lines.append("# $ conda create --name <env> --file <this file>")
        lines.append(f"# platform: {result.platform}")
        lines.append("@EXPLICIT")
        for pkg in result.packages:
            if md5 and pkg.md5:
                lines.append(f"{pkg.url}#{pkg.md5}")
            else:
                lines.append(pkg.url)
    return "\n".join(lines) + "\n"


def _format_text(
    results: list[SolveResult],
    *,
    no_builds: bool,
    no_channels: bool,
) -> str:
    """Format results as human-readable text."""
    lines: list[str] = []
    for result in results:
        if result.error:
            lines.append(f"# Error ({result.platform}): {result.error}")
            continue
        for pkg in result.packages:
            if no_channels:
                if no_builds:
                    lines.append(f"{pkg.name}=={pkg.version}")
                else:
                    lines.append(
                        f"{pkg.name}=={pkg.version}={pkg.build}"
                    )
            else:
                if no_builds:
                    lines.append(
                        f"{pkg.channel}::{pkg.name}=={pkg.version}"
                    )
                else:
                    lines.append(
                        f"{pkg.channel}/{pkg.subdir}"
                        f"::{pkg.name}=={pkg.version}={pkg.build}"
                    )
    return "\n".join(lines) + "\n"


def cmd_solve(args: argparse.Namespace):
    """Resolve packages and write output to stdout."""
    context.__init__(argparse_args=args)

    deps: list[str] = []
    for fpath in args.files:
        with open(fpath, "rb") as f:
            req = SolveRequest.from_environment_yml(f.read())
            deps.extend(req.dependencies)

    specs = [s.strip("\"'") for s in args.specs]
    deps.extend(specs)

    if not deps:
        print(
            "Provide an environment.yml (--file) or package specs.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    channels = list(context.channels)
    request = SolveRequest(
        channels=channels,
        dependencies=deps,
        platforms=args.platforms,
    )

    results = solve(request)

    if args.explicit:
        sys.stdout.write(_format_explicit(results, md5=args.md5))
    elif args.no_builds or args.no_channels:
        sys.stdout.write(
            _format_text(
                results,
                no_builds=args.no_builds,
                no_channels=args.no_channels,
            )
        )
    else:
        output = [r.to_dict() for r in results]
        json.dump(output, sys.stdout, indent=2)
        print()


def cmd_serve(args: argparse.Namespace):
    """Start the HTTP API server via uvicorn."""
    uvicorn.run(
        "conda_solver_api.app:app", host=args.host, port=args.port
    )


def main():
    """Standalone entry point for the ``conda-solver-api`` script."""
    parser = argparse.ArgumentParser(
        description="Resolve conda environments to fully pinned "
        "packages with SHA256 hashes.",
    )
    configure_parser(parser)
    args = parser.parse_args()
    execute(args)


if __name__ == "__main__":
    main()
