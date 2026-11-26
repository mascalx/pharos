"""Hierarchical geographic area directory from Geofabrik's official GeoJSON.

This is geographic coverage metadata only, not a downloadable Protomaps
archive. Each area bbox is derived from publisher GeoJSON polygon coordinates
and can be cut out of a separately verified Protomaps planet basemap.
"""
from __future__ import annotations

import json
import os
import tempfile
import math
import re
import threading
import time
import urllib.request
from pathlib import Path

from .kiwix_runtime import _PublisherRedirects
from .maps_search import valid_source_url

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
MAX_BYTES = 24 * 1024 * 1024
MAX_AREAS = 1200
TTL = 86400
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,90}\Z")
_lock = threading.RLock()
_cache = {"expires": 0., "items": None}

class AreaCatalogError(ValueError):
    pass

def _walk_coordinates(value, bounds):
    if isinstance(value, list):
        if (len(value) >= 2 and all(isinstance(x, (int, float)) and
                not isinstance(x, bool) and math.isfinite(x) for x in value[:2])):
            lon, lat = value[:2]
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise AreaCatalogError("maps_area_invalid")
            bounds[0] = min(bounds[0], lon)
            bounds[1] = min(bounds[1], lat)
            bounds[2] = max(bounds[2], lon)
            bounds[3] = max(bounds[3], lat)
        else:
            for child in value:
                _walk_coordinates(child, bounds)

def parse_catalog(payload):
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise AreaCatalogError("maps_areas_invalid")
    features = payload.get("features")
    if not isinstance(features, list) or len(features) > MAX_AREAS:
        raise AreaCatalogError("maps_areas_invalid")
    areas = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        geom = feature.get("geometry")
        if not isinstance(props, dict) or not isinstance(geom, dict):
            continue
        key, parent, name = props.get("id"), props.get("parent"), props.get("name")
        if not isinstance(key, str) or not ID.fullmatch(key):
            continue
        if parent is not None and (not isinstance(parent, str) or not ID.fullmatch(parent)):
            continue
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            continue
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        bounds = [180., 90., -180., -90.]
        _walk_coordinates(geom.get("coordinates"), bounds)
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            continue
        # Keep dateline-crossing regions visible for navigation, but disable
        # one-piece extraction until an explicit two-part splitter exists.
        downloadable = bounds[2] - bounds[0] <= 180
        bbox = [round(v, 5) for v in bounds]
        # Conservative presets by hierarchy. The map can always be downloaded
        # again at a higher zoom in a later release.
        zoom = 9 if parent is None else 11
        urls = props.get("urls") if isinstance(props.get("urls"), dict) else {}
        pbf_url = valid_source_url(urls.get("pbf"))
        areas.append({"id": key, "parent": parent, "name": name.strip(),
                      "bbox": bbox, "zoom": zoom,
                      "downloadable": downloadable, "pbf_url": pbf_url})
    ids = {area["id"] for area in areas}
    if len(ids) != len(areas):
        raise AreaCatalogError("maps_areas_invalid")
    # Keep hierarchy navigable even if a parent was excluded for the dateline.
    for area in areas:
        if area["parent"] not in ids:
            area["parent"] = None
        elif next((x["parent"] for x in areas if x["id"] == area["parent"]), None) is not None:
            area["zoom"] = 12
    return sorted(areas, key=lambda x: (x["name"].casefold(), x["id"]))

def _fetch():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublisherRedirects())
    req = urllib.request.Request(INDEX_URL, headers={"User-Agent": "PHAROS-Maps/0.3"})
    with opener.open(req, timeout=25) as response:
        if response.status != 200:
            raise AreaCatalogError("maps_areas_unavailable")
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise AreaCatalogError("maps_areas_invalid")
    return parse_catalog(json.loads(content.decode("utf-8")))

def _read_disk_cache(path: Path):
    # Generated normalized area metadata, not executable content.
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        items = record.get("items") if isinstance(record, dict) and record.get("schema") == 1 else None
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_AREAS:
            return None
        keys = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not ID.fullmatch(item["id"]):
                return None
            if item["id"] in keys:
                return None
            keys.add(item["id"])
            if not isinstance(item.get("name"), str) or not 1 <= len(item["name"]) <= 120:
                return None
            parent = item.get("parent")
            if parent is not None and (not isinstance(parent, str) or not ID.fullmatch(parent)):
                return None
            if item.get("pbf_url") is not None and valid_source_url(item["pbf_url"]) is None:
                return None
            b = item.get("bbox")
            if (not isinstance(b, list) or len(b) != 4 or
                    any(type(n) not in (int, float) or not math.isfinite(n) for n in b) or
                    not -180 <= b[0] < b[2] <= 180 or not -90 <= b[1] < b[3] <= 90):
                return None
        return items
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def _write_disk_cache(path: Path, items):
    # Cache changes must never overwrite operational databases or PMTiles.
    if path.is_symlink():
        return
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         prefix=".maps-geography-", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            tmp = Path(stream.name)
            json.dump({"schema": 1, "items": items}, stream, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def areas(*, refresh=False, cache_path: Path | None = None):
    with _lock:
        if _cache["items"] is not None and (not refresh or time.monotonic() < _cache["expires"]):
            return {"items": list(_cache["items"]), "status": "available"}
    try:
        fresh = _fetch()
        if not fresh:
            raise AreaCatalogError("maps_areas_unavailable")
    except (OSError, ValueError, UnicodeError, TimeoutError) as exc:
        with _lock:
            if _cache["items"] is not None:
                return {"items": list(_cache["items"]), "status": "cached"}
        cached = _read_disk_cache(cache_path) if cache_path is not None else None
        if cached is not None:
            with _lock:
                _cache["items"] = cached
                _cache["expires"] = time.monotonic() + TTL
            return {"items": list(cached), "status": "cached"}
        return {"items": [], "status": "unavailable"}
    if cache_path is not None:
        _write_disk_cache(cache_path, fresh)
    with _lock:
        _cache["items"] = fresh
        _cache["expires"] = time.monotonic() + TTL
    return {"items": list(fresh), "status": "available"}

def get_area(area_id, *, cache_path: Path | None = None):
    if not isinstance(area_id, str) or not ID.fullmatch(area_id):
        raise AreaCatalogError("maps_region_unknown")
    # Only allow IDs present in our retrieved official publisher directory.
    match = next((area for area in areas(cache_path=cache_path)["items"] if area["id"] == area_id), None)
    if match is None:
        raise AreaCatalogError("maps_region_unknown")
    if not match.get("downloadable", True):
        raise AreaCatalogError("maps_region_dateline")
    return dict(match)
