"""Read-only official Protomaps build catalog.

Never invent region files: the publisher distributes planet basemaps, while
PHAROS extracts user-selected rectangles in separate background jobs.
Only official build names scraped from the official builds listing may be
probed. Each candidate must return a valid PMTiles v3 header through a
bounded HTTP byte-range request before being offered.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import threading
import time
import urllib.request
import urllib.error

from .kiwix_runtime import _PublisherRedirects

INDEX_URL = "https://maps.protomaps.com/builds/"
BUILD_ORIGIN = "https://build.protomaps.com/"
NAME = re.compile(r"(?<![0-9])20[0-9]{6}\.pmtiles(?![A-Za-z0-9])")
MAX_HTML = 2 * 1024 * 1024
TTL = 900
_lock = threading.RLock()
_cache = {"expires": 0.0, "data": None}


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _PublisherRedirects())


def _listing():
    request = urllib.request.Request(INDEX_URL, headers={"User-Agent": "PHAROS-Maps/0.2"})
    with _opener().open(request, timeout=12) as result:
        if result.status != 200:
            raise ValueError("maps_catalog_unavailable")
        if result.headers.get("Content-Length", "0").isdigit() and int(
            result.headers.get("Content-Length", "0")) > MAX_HTML:
            raise ValueError("maps_catalog_invalid")
        document = result.read(MAX_HTML + 1)
        if len(document) > MAX_HTML:
            raise ValueError("maps_catalog_invalid")
    names = sorted(set(NAME.findall(document.decode("utf-8", "replace"))), reverse=True)
    return names[:14]


def _probe(name):
    url = BUILD_ORIGIN + name
    request = urllib.request.Request(url, headers={
        "User-Agent": "PHAROS-Maps/0.2", "Range": "bytes=0-7"})
    with _opener().open(request, timeout=12) as result:
        if result.status != 206:
            return False
        content_range = result.headers.get("Content-Range", "")
        if not re.fullmatch(r"bytes 0-7/[0-9]+", content_range):
            return False
        return result.read(8) == b"PMTiles" + bytes([3])


def _candidate_names():
    # The official builds webpage may be a JS app with no names in its static
    # HTML. The publisher retains daily builds for roughly one week: discover
    # candidates by date and confirm the actual PMTiles header via HTTP Range.
    today = datetime.now(timezone.utc).date()
    candidates = [(today - timedelta(days=offset)).strftime("%Y%m%d") + ".pmtiles"
                  for offset in range(9)]
    try:
        candidates.extend(_listing())
    except (OSError, ValueError, TimeoutError):
        pass
    return list(dict.fromkeys(candidates))[:18]


def builds(*, refresh=False):
    now = time.monotonic()
    with _lock:
        if not refresh and _cache["data"] is not None and now < _cache["expires"]:
            return dict(_cache["data"])
    candidates = _candidate_names()
    verified = []
    # Probe a small bounded group concurrently; we must never infer
    # availability from a date-shaped name alone.
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(_probe, name): name for name in candidates}
        for future in as_completed(pending):
            try:
                if future.result():
                    verified.append(pending[future])
            except (OSError, ValueError, TimeoutError):
                pass
    available = [{"id": name, "date": name[:8],
                  "source_url": BUILD_ORIGIN + name}
                 for name in sorted(set(verified), reverse=True)[:3]]
    result = {"items": available,
              "status": "available" if available else "unavailable",
              "checked_at": datetime.now(timezone.utc).isoformat()}
    with _lock:
        _cache["data"] = result
        _cache["expires"] = time.monotonic() + (TTL if available else 90)
    return dict(result)


def validate_source(source_id):
    if not isinstance(source_id, str) or not re.fullmatch(r"20[0-9]{6}\.pmtiles", source_id):
        raise ValueError("maps_source_invalid")
    # Verify again when a queued job starts: dates may expire on the publisher.
    try:
        checked = _probe(source_id)
    except (OSError, ValueError, TimeoutError) as exc:
        raise ValueError("maps_source_unavailable") from exc
    if not checked:
        raise ValueError("maps_source_unavailable")
    return BUILD_ORIGIN + source_id
