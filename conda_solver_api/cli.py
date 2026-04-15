"""CLI for conda-solver-api.

Exposes ``configure_parser`` and ``execute`` for the conda plugin hook
(``conda solver-api ...``), and ``main`` for standalone use via the
``conda-solver-api`` script entry point.

The CLI has two subcommands:

- ``solve`` — resolve an environment.yml or inline specs to JSON
- ``serve`` — start the HTTP API server (uvicorn)
"""
from __future__ import annotations

import argparse
import json
import sys

import uvicorn

from .resolve import SolveRequest, solve


def configure_parser(parser: argparse.ArgumentParser):
    """Add ``solve`` and ``serve`` subcommands to *parser*.

    Used by both the conda plugin hook and the standalone ``main()``.
    """
    sub = parser.add_subparsers(dest="subcommand")

    solve_p = sub.add_parser(
        "solve", help="Solve an environment.yml or inline specs"
    )
    solve_p.add_argument(
        "-f", "--file", default=None, help="Path to environment.yml",
    )
    solve_p.add_argument(
        "-c", "--channel", action="append", default=[], dest="channels",
    )
    solve_p.add_argument(
        "-p", "--platform", action="append", default=[], dest="platforms",
    )
    solve_p.add_argument("specs", nargs="*", help="Inline package specs")

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


def cmd_solve(args: argparse.Namespace):
    """Resolve packages and write JSON to stdout."""
    if args.file:
        with open(args.file, "rb") as f:
            request = SolveRequest.from_environment_yml(
                f.read(),
                channels=args.channels or None,
                platforms=args.platforms or None,
            )
    elif args.specs:
        request = SolveRequest(
            channels=args.channels or ["defaults"],
            dependencies=args.specs,
            platforms=args.platforms,
        )
    else:
        print(
            "Provide an environment.yml file or package specs.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    results = solve(request)
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
