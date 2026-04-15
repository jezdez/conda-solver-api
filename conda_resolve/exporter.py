"""Conda environment exporter that outputs full package metadata.

The ``resolve-json`` format includes sha256, md5, urls, sizes,
dependencies, and constrains for every resolved package — the same
level of detail the HTTP API returns.
"""
from __future__ import annotations

import json

from conda.models.environment import Environment


def _record_to_dict(record) -> dict:
    """Extract the fields we care about from a PackageRecord."""
    return {
        "name": record.name,
        "version": str(record.version),
        "build": record.build,
        "build_number": record.build_number,
        "channel": (
            record.channel.canonical_name if record.channel else ""
        ),
        "subdir": record.subdir or "",
        "url": record.url or "",
        "sha256": record.sha256 or "",
        "md5": record.md5 or "",
        "size": getattr(record, "size", None),
        "depends": list(record.depends) if record.depends else [],
        "constrains": list(record.constrains) if record.constrains else [],
    }


def export_resolve_json(env: Environment) -> str:
    """Export an ``Environment`` as JSON with full package metadata."""
    packages = sorted(env.explicit_packages, key=lambda r: r.name)
    result = {
        "platform": env.platform,
        "packages": [_record_to_dict(r) for r in packages],
    }
    return json.dumps(result, indent=2)
