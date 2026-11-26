"""Offline map archive inventory and safe byte-range delivery.

This subsystem never contacts the Internet. Archives are data, not executable
code, and live under storage.data_dir/maps/regions. No automated extraction,
routing or rendering is claimed here.
"""
from __future__ import annotations

import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse, Response

from .storage import check_data_volume
from .maps_coverage import archived_regions, redundancy, comparison
from .maps_viewer_assets import (asset_directory, status as viewer_asset_status, ASSET_FILES,
                                 FONT_STACK, FONT_BLOBS, font_directory, labels_ready)

_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.pmtiles\Z")
_MAX_RANGE = 16 * 1024 * 1024
_PM_MAGIC = b"PMTiles"
_PM_VERSION = 3


def map_directory(config) -> Path:
    return config.storage.data_dir / "maps" / "regions"


def _available(config) -> None:
    if not check_data_volume(config.storage).available:
        raise HTTPException(503, detail="data_volume_unavailable")


def _archive(config, filename: str) -> Path:
    if not _FILENAME.fullmatch(filename) or ".." in filename:
        raise HTTPException(422, detail="map_filename_invalid")
    base = map_directory(config)
    path = base / filename
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, detail="map_not_found")
    # Prevent unintended traversal through symlinked parent directories.
    if base.resolve() not in path.resolve().parents:
        raise HTTPException(404, detail="map_not_found")
    return path


def _valid_pmtiles(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(8) == _PM_MAGIC + bytes([_PM_VERSION])
    except OSError:
        return False


def _viewer_header(path: Path) -> dict | None:
    """Read PMTiles v3 viewport bounds without guessing the region geometry."""
    try:
        with path.open("rb") as stream:
            data = stream.read(127)
        if len(data) < 127 or data[:8] != b"PMTiles" + bytes([3]):
            return None
        tile_type, minimum, maximum = data[99], data[100], data[101]
        west, south, east, north = (v / 1e7 for v in struct.unpack_from("<iiii", data, 102))
        if (not 0 <= minimum <= maximum <= 22 or tile_type not in (1, 2, 3, 4)
                or not -180 <= west < east <= 180
                or not -90 <= south < north <= 90):
            return None
        return {"bounds": [west, south, east, north],
                "minzoom": minimum, "maxzoom": maximum,
                "tile_type": tile_type, "vector": tile_type == 1}
    except (OSError, ValueError, struct.error):
        return None


def delete_local_map(config, filename: str, jobs) -> dict:
    """Delete one explicitly named completed archive and its matching sidecar.

    Never delete an active/pending download, arbitrary paths or other map data.
    Caller must enforce same-origin mutation and explicit UI confirmation.
    """
    _available(config)
    with jobs._lock:
        path = _archive(config, filename)
        # Guard the durable queue as well as running jobs. It is possible for
        # the worker to be preparing an archive while the old one is visible.
        with jobs._db() as db:
            active = db.execute("""SELECT 1 FROM jobs WHERE kind='download-map'
                AND label=? AND status IN ('queued', 'running') LIMIT 1""",
                (path.stem,)).fetchone()
        if active:
            raise HTTPException(409, detail="maps_delete_download_active")
        size = path.stat().st_size
        sidecar = path.with_suffix(".json")
        # First remove the archive. A sidecar orphan is not used for rendering;
        # it must never be allowed to delete another file via symlink.
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="map_not_found") from exc
        except OSError as exc:
            raise HTTPException(409, detail="maps_delete_failed") from exc
        if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
            return {"deleted": filename, "size_bytes": size,
                    "metadata_removed": False}
        if sidecar.is_file():
            try:
                sidecar.unlink()
            except OSError:
                # An orphan metadata file is harmless and no longer appears
                # in the inventory. Do not claim it was removed.
                return {"deleted": filename, "size_bytes": size,
                        "metadata_removed": False}
        return {"deleted": filename, "size_bytes": size,
                "metadata_removed": True}


def router(config) -> APIRouter:
    api = APIRouter()

    @api.get("/api/maps/viewer-assets/status")
    def viewer_status():
        return viewer_asset_status(config)

    @api.get("/api/maps/viewer-assets/{filename}")
    def viewer_file(filename: str):
        if filename not in ASSET_FILES:
            raise HTTPException(404, detail="map_asset_not_found")
        if not viewer_asset_status(config)["installed"]:
            raise HTTPException(503, detail="map_assets_not_installed")
        path = asset_directory(config) / filename
        return FileResponse(path, media_type=("text/css" if filename.endswith(".css")
                            else "text/javascript"),
                            headers={"Cache-Control": "private, max-age=86400",
                                     "X-Content-Type-Options": "nosniff"})

    @api.get("/api/maps/fonts/{fontstack}/{range_name}")
    def offline_glyphs(fontstack: str, range_name: str):
        # Only the locally verified fontstack/ranges are exposed.
        if fontstack != FONT_STACK or range_name not in FONT_BLOBS:
            raise HTTPException(404, detail="maps_font_not_found")
        if not labels_ready(config):
            raise HTTPException(503, detail="maps_fonts_not_installed")
        path = font_directory(config) / range_name
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, detail="maps_font_not_found")
        return FileResponse(path, media_type="application/x-protobuf",
                            headers={"Cache-Control":"private, max-age=86400",
                                     "X-Content-Type-Options":"nosniff"})

    @api.get("/api/maps/regions")
    def regions():
        _available(config)
        base = map_directory(config)
        if not base.is_dir():
            return {"items": []}
        result = []
        coverage = archived_regions(base)
        known = {item["id"]: item for item in coverage}
        for path in sorted(base.glob("*.pmtiles")):
            if path.is_symlink() or not path.is_file() or not _valid_pmtiles(path):
                continue
            if not _FILENAME.fullmatch(path.name):
                continue
            stat = path.stat()
            item = {"id": path.name, "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc).isoformat(),
                    "archive_url": "/api/maps/regions/" + path.name + "/archive"}
            meta = known.get(path.stem)
            if meta:
                item["coverage"] = {"bbox": meta["bbox"], "maxzoom": meta["maxzoom"],
                                    "source": meta["source"],
                                    **redundancy(meta, coverage)}
            viewport = _viewer_header(path)
            if viewport:
                item["viewer"] = viewport
            result.append(item)
        return {"items": result}

    @api.get("/api/maps/regions/{filename}/check")
    def inspect_archive(filename: str):
        """Non-destructive PMTiles header/directory integrity and range checks.

        This does not claim to validate all vector tiles, but distinguishes
        malformed archives from browser/HTTP/runtime problems without a
        full-file scan or remote traffic.
        """
        _available(config)
        path = _archive(config, filename)
        size = path.stat().st_size
        if size < 127:
            return {"valid": False, "reason": "maps_check_short_header"}
        with path.open("rb") as stream:
            header = stream.read(127)
            if header[:8] != _PM_MAGIC + bytes([_PM_VERSION]):
                return {"valid": False, "reason": "maps_check_invalid_header"}
            try:
                root_offset, root_length, json_offset, json_length, leaf_offset, leaf_length, tile_offset, tile_length = struct.unpack_from("<QQQQQQQQ", header, 8)
            except struct.error:
                return {"valid": False, "reason": "maps_check_invalid_header"}
            sections = ((root_offset, root_length), (json_offset, json_length),
                        (leaf_offset, leaf_length), (tile_offset, tile_length))
            if (root_length == 0 or root_length > _MAX_RANGE or
                    any(offset > size or length > size - offset for offset, length in sections)):
                return {"valid": False, "reason": "maps_check_invalid_directory"}
            stream.seek(root_offset)
            root = stream.read(root_length)
            if len(root) != root_length:
                return {"valid": False, "reason": "maps_check_invalid_directory"}
        return {"valid": True, "size_bytes": size,
                "header": _viewer_header(path),
                "checked": ["header", "root_directory_bounds", "root_directory_read"]}

    @api.get("/api/maps/regions/{filename}/archive")
    def archive(filename: str, range_header: Annotated[str | None, Header(alias="Range")] = None):
        _available(config)
        path = _archive(config, filename)
        if not _valid_pmtiles(path):
            raise HTTPException(422, detail="map_archive_invalid")
        size = path.stat().st_size
        # Explicit capped single-range support is important for PMTiles
        # browsers and prevents a huge response from an accidental full GET.
        if range_header is None:
            raise HTTPException(416, detail="map_range_required",
                                headers={"Content-Range": f"bytes */{size}"})
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            raise HTTPException(416, detail="map_range_invalid",
                                headers={"Content-Range": f"bytes */{size}"})
        start = int(match.group(1))
        end = min(int(match.group(2)) if match.group(2) else start + _MAX_RANGE - 1, size - 1)
        if start >= size or end < start or end - start + 1 > _MAX_RANGE:
            raise HTTPException(416, detail="map_range_invalid",
                                headers={"Content-Range": f"bytes */{size}"})
        with path.open("rb") as stream:
            stream.seek(start)
            payload = stream.read(end - start + 1)
        return Response(payload, status_code=206, media_type="application/octet-stream",
                        headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                 "Accept-Ranges": "bytes", "Content-Length": str(len(payload)),
                                 "Cache-Control": "private, no-store"})

    return api
