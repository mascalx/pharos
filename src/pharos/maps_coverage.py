"""Conservative local-only coverage diagnostics for PHAROS PMTiles archives.

A rectangular footprint is NOT a claim of complete vector tile coverage.
Only validated files with matching trusted sidecar metadata participate.
No files are deleted, reindexed or downloaded by this component.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

_FILENAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,90}\Z")
MAX_META = 4096
EPS = 1e-6


def valid_bbox(bounds):
    return (isinstance(bounds, list) and len(bounds) == 4 and
            all(type(v) in (int, float) and math.isfinite(v) for v in bounds) and
            -180 <= bounds[0] < bounds[2] <= 180 and
            -90 <= bounds[1] < bounds[3] <= 90)


def contains(outer, inner):
    return (valid_bbox(outer) and valid_bbox(inner) and
            outer[0] <= inner[0] + EPS and outer[1] <= inner[1] + EPS and
            outer[2] + EPS >= inner[2] and outer[3] + EPS >= inner[3])


def metadata_for_archive(path: Path):
    """Only return metadata for a complete local PMTiles v3 archive."""
    if path.is_symlink() or not path.is_file() or not path.name.endswith(".pmtiles"):
        return None
    identity = path.name[:-8]
    if not _FILENAME.fullmatch(identity):
        return None
    sidecar = path.with_suffix(".json")
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size > MAX_META:
        return None
    try:
        with path.open("rb") as stream:
            header = stream.read(127)
        if len(header) < 127 or header[:8] != b"PMTiles" + bytes([3]):
            return None
        maximum = header[101]
        if maximum > 22 or header[100] > maximum:
            return None
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        if (not isinstance(meta, dict) or meta.get("schema") != 1 or
                meta.get("id") != identity or not valid_bbox(meta.get("bbox")) or
                not isinstance(meta.get("source"), str) or
                not re.fullmatch(r"20\d{6}\.pmtiles", meta["source"])):
            return None
        return {"id": identity, "bbox": meta["bbox"], "maxzoom": maximum,
                "source": meta["source"], "size_bytes": path.stat().st_size}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def archived_regions(directory: Path):
    if not directory.is_dir() or directory.is_symlink():
        return []
    return [meta for path in sorted(directory.glob("*.pmtiles"))
            if not path.is_symlink()
            if (meta := metadata_for_archive(path)) is not None]


def comparison(target, archived):
    """Warnings are advisory; no automatic deletion or download blocking."""
    bounds = target.get("bbox")
    zoom = target.get("zoom")
    if not valid_bbox(bounds) or type(zoom) is not int or not 0 <= zoom <= 22:
        return {"status": "unknown", "covering": [], "higher_detail": []}
    covering = [m for m in archived if contains(m["bbox"], bounds)]
    sufficient = [m for m in covering if m["maxzoom"] >= zoom]
    # A greater zoom in a contained, smaller archive may warrant keeping it
    # even if the larger area also exists. Only report, never auto-remove.
    detailed = [m for m in archived if contains(bounds, m["bbox"]) and
                m["maxzoom"] > zoom]
    return {
        "status": "covered" if sufficient else "lower_detail" if covering else "new",
        "covering": [{"id": m["id"], "maxzoom": m["maxzoom"],
                      "source": m["source"]} for m in covering],
        "higher_detail": [{"id": m["id"], "maxzoom": m["maxzoom"]}
                          for m in detailed],
    }


def redundancy(archive, all_archives):
    others = [m for m in all_archives if m["id"] != archive["id"]
              and contains(m["bbox"], archive["bbox"])
              and m["maxzoom"] >= archive["maxzoom"]]
    # Exact source match permits a stronger warning; different snapshots
    # may have updates or features not reflected solely in the zoom number.
    return {"potentially_redundant": bool(others),
            "same_source": any(m["source"] == archive["source"] for m in others),
            "covered_by": [{"id": m["id"], "maxzoom": m["maxzoom"]}
                           for m in others]}
