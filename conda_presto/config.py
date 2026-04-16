"""Application configuration via environment variables.

All settings have sensible defaults and can be overridden by setting
the corresponding ``CONDA_PRESTO_*`` environment variable.

Channels and platforms:
    ``CONDA_PRESTO_CHANNELS``
        Comma-separated fallback channels (default: ``conda-forge``).
    ``CONDA_PRESTO_PLATFORMS``
        Comma-separated platforms to pre-warm on startup
        (default: ``linux-64,osx-arm64,osx-64``).

Server tuning:
    ``CONDA_PRESTO_CONCURRENCY``
        Max concurrent solve requests via thread limiter (default: ``4``).
    ``CONDA_PRESTO_WORKERS``
        Process pool size for multi-platform solves
        (default: ``min(4, cpu_count)``).
    ``CONDA_PRESTO_MAX_BODY_BYTES``
        Max request body size in bytes (default: ``1048576``).
    ``CONDA_PRESTO_HOST``
        Default bind address for ``--serve`` (default: ``127.0.0.1``).
    ``CONDA_PRESTO_PORT``
        Default port for ``--serve`` (default: ``8000``).

HTTP middleware:
    ``CONDA_PRESTO_RATE_LIMIT``
        Max requests per minute per client (default: ``300``).
        Set to ``0`` to disable rate limiting.
    ``CONDA_PRESTO_CORS_ORIGINS``
        Comma-separated allowed CORS origins (default: ``*``).
    ``CONDA_PRESTO_LOG_LEVEL``
        Application log level (default: ``INFO``).

Cross-platform virtual packages:
    ``CONDA_PRESTO_GLIBC_VERSION``
        Virtual ``__glibc`` version for Linux solves (default: ``2.17``).
    ``CONDA_PRESTO_LINUX_VERSION``
        Virtual ``__linux`` version for Linux solves (default: ``5.15``).
    ``CONDA_PRESTO_OSX_VERSION``
        Virtual ``__osx`` version for macOS solves (default: ``11.0``).
"""
from __future__ import annotations

import os

DEFAULT_CHANNELS = os.environ.get(
    "CONDA_PRESTO_CHANNELS", "conda-forge"
).split(",")

DEFAULT_PLATFORMS = os.environ.get(
    "CONDA_PRESTO_PLATFORMS", "linux-64,osx-arm64,osx-64"
).split(",")

MAX_BODY_BYTES = int(
    os.environ.get("CONDA_PRESTO_MAX_BODY_BYTES", 1_024 * 1_024)
)

MAX_CONCURRENCY = int(os.environ.get("CONDA_PRESTO_CONCURRENCY", 4))

MAX_WORKERS = int(
    os.environ.get(
        "CONDA_PRESTO_WORKERS",
        min(4, os.cpu_count() or 4),
    )
)

GLIBC_VERSION = os.environ.get("CONDA_PRESTO_GLIBC_VERSION", "2.17")
LINUX_VERSION = os.environ.get("CONDA_PRESTO_LINUX_VERSION", "5.15")
OSX_VERSION = os.environ.get("CONDA_PRESTO_OSX_VERSION", "11.0")

DEFAULT_HOST = os.environ.get("CONDA_PRESTO_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("CONDA_PRESTO_PORT", 8000))

RATE_LIMIT = int(os.environ.get("CONDA_PRESTO_RATE_LIMIT", 300))
CORS_ORIGINS = os.environ.get("CONDA_PRESTO_CORS_ORIGINS", "*").split(",")
LOG_LEVEL = os.environ.get("CONDA_PRESTO_LOG_LEVEL", "INFO")
