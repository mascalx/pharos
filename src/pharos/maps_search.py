"""Offline-first geographical search: independent SQLite/FTS5/RTree indexes.

The index is *derived* from official Geofabrik OSM extracts, not from PMTiles.
It never contacts the network during queries, never touches Qdrant/KB, and
publishes new generations only after successful full construction.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urljoin

from .kiwix_runtime import _PublisherRedirects
from .storage import check_data_volume
from .maps_geosearch import search_index, collapse_segments

AREA_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,90}\Z")
PBF_PATH = re.compile(r"/(?:[a-z0-9_-]+/)*[a-z0-9_-]+-latest\.osm\.pbf\Z")
MAX_SOURCE_BYTES = 12 * 1024 ** 3
CATEGORIES = ("place", "address", "street", "poi")
INDEX_SCHEMA = "3"
COMPATIBLE_INDEX_SCHEMAS = {"1", "2", "3"}


class GeoSearchError(ValueError):
    pass


def valid_source_url(value: str | None) -> str | None:
    """Only the exact official public Geofabrik PBF origin is accepted."""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname != "download.geofabrik.de"
                or url.port not in (None, 443) or url.username is not None
                or url.password is not None or url.query or url.fragment
                or not PBF_PATH.fullmatch(url.path)):
            return None
        return value
    except ValueError:
        return None


def _area_id(value):
    if not isinstance(value, str) or not AREA_ID.fullmatch(value):
        raise GeoSearchError("maps_search_area_invalid")
    return value


def index_directory(config) -> Path:
    return config.storage.database_dir / "maps-search"


def index_file(config, area_id: str) -> Path:
    return index_directory(config) / (_area_id(area_id) + ".sqlite3")


def _source_directory(config) -> Path:
    return config.storage.data_dir / "maps" / "search-sources"


def _safe_directory(path: Path):
    if path.is_symlink():
        raise GeoSearchError("maps_search_storage_unsafe")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise GeoSearchError("maps_search_storage_unsafe")


def _abort_if(cancelled):
    if cancelled():
        raise GeoSearchError("maps_search_cancelled")


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _PublisherRedirects())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Inspect Geofabrik redirects without following a potentially large PBF."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _snapshot_source_url(latest_url: str, *, opener=None) -> str:
    """Resolve Geofabrik's -latest redirect into its immutable dated PBF.

    Since September 2025 Geofabrik redirects *-latest.osm.pbf to the
    dated snapshot. Only the dated file has a corresponding published
    .osm.pbf.md5 sidecar. Do not fetch the PBF body just to resolve it.
    Redirects are manually validated against the original region/host;
    the subsequent data download still uses the existing safe redirect
    handler to follow trusted HTTPS public mirrors.
    """
    if valid_source_url(latest_url) is None:
        raise GeoSearchError("maps_search_source_unavailable")
    origin = urlsplit(latest_url)
    directory, basename = origin.path.rsplit("/", 1)
    prefix = basename[:-len("-latest.osm.pbf")]
    base_paths = {
        f"{directory}/{prefix}-latest.osm.pbf",
        f"{directory}/{prefix}/{prefix}-latest.osm.pbf",
    }
    dated = re.compile(
        r"\A" + re.escape(directory) + r"(?:/" + re.escape(prefix) + r")?/"
        + re.escape(prefix) + r"-[0-9]{6}\.osm\.pbf\Z"
    )
    probe = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    current = latest_url
    for _ in range(4):
        request = urllib.request.Request(
            current, method="HEAD",
            headers={"User-Agent": "PHAROS/0.7 (offline-search)"},
        )
        try:
            with probe.open(request, timeout=35) as response:
                if response.status != 200:
                    raise GeoSearchError("maps_search_snapshot_unavailable")
                # Older Geofabrik configurations could serve -latest
                # directly. Fall back to its checksum in that case.
                return current
        except urllib.error.HTTPError as exc:
            if exc.code in (405, 501):
                # Some publisher frontends reject HEAD. A one-byte GET can
                # still disclose a redirect without downloading the PBF.
                fallback = urllib.request.Request(
                    current, headers={"User-Agent": "PHAROS/0.7 (offline-search)",
                                      "Range": "bytes=0-0"})
                try:
                    with probe.open(fallback, timeout=35) as response:
                        if response.status not in (200, 206):
                            raise GeoSearchError("maps_search_snapshot_unavailable")
                        return current
                except urllib.error.HTTPError as fallback_error:
                    exc = fallback_error
                except (OSError, ValueError) as fallback_error:
                    if isinstance(fallback_error, GeoSearchError):
                        raise
                    raise GeoSearchError("maps_search_snapshot_unavailable") from fallback_error
            if exc.code not in (301, 302, 303, 307, 308):
                raise GeoSearchError("maps_search_snapshot_unavailable") from exc
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise GeoSearchError("maps_search_snapshot_unavailable") from exc
            next_url = urljoin(current, location)
            try:
                parts = urlsplit(next_url)
                allowed = (parts.scheme == "https"
                    and parts.hostname == "download.geofabrik.de"
                    and parts.port in (None, 443)
                    and parts.username is None and parts.password is None
                    and not parts.query and not parts.fragment
                    and (parts.path in base_paths or dated.fullmatch(parts.path)))
            except ValueError:
                allowed = False
            if not allowed or next_url == current:
                raise GeoSearchError("maps_search_snapshot_unavailable") from exc
            if dated.fullmatch(parts.path):
                return next_url
            current = next_url
        except (OSError, ValueError) as exc:
            if isinstance(exc, GeoSearchError):
                raise
            raise GeoSearchError("maps_search_snapshot_unavailable") from exc
    raise GeoSearchError("maps_search_snapshot_unavailable")


def _parse_publisher_md5(data: bytes, snapshot_url: str) -> str:
    """Accept only a genuine checksum line for the precise dated PBF."""
    if len(data) > 256:
        raise GeoSearchError("maps_search_checksum_unavailable")
    try:
        value = data.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise GeoSearchError("maps_search_checksum_unavailable") from exc
    # Geofabrik publishes '<32 hex>  <snapshot-file>'. Never accept an
    # unrelated file's hash or an HTML error page containing hex strings.
    match = re.fullmatch(r"([a-fA-F0-9]{32})\s+\*?([A-Za-z0-9_.-]+)", value)
    if not match or match.group(2) != snapshot_url.rsplit("/", 1)[-1]:
        raise GeoSearchError("maps_search_checksum_unavailable")
    return match.group(1).lower()


def _curl_publisher_md5(snapshot_url: str) -> bytes:
    """Small checksum-only fallback for Geofabrik's broken 308 rewrite.

    curl succeeded from the user's Ubuntu VM for this exact publisher URL
    when Python urllib followed a 308 into a duplicated, non-existent path.
    This path is reached only after urllib returns HTTP 404. No shell, no
    redirects, an official fixed host, strict time/size bounds and TLS checks.
    A missing curl or unsuccessful response fails closed; integrity is never
    bypassed. The large PBF download still uses the existing Python worker.
    """
    parts = urlsplit(snapshot_url)
    if (parts.scheme != "https" or parts.hostname != "download.geofabrik.de"
            or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443)
            or not re.fullmatch(r"/(?:[a-z0-9_-]+/)*[a-z0-9_-]+-[0-9]{6}\.osm\.pbf", parts.path)
            or parts.query or parts.fragment):
        raise GeoSearchError("maps_search_checksum_unavailable")
    checksum_url = snapshot_url + ".md5"
    args = [
        "curl", "--silent", "--show-error", "--fail",
        "--max-time", "25", "--connect-timeout", "10",
        "--max-filesize", "256", "--max-redirs", "0",
        "--proto", "=https", "--write-out", "\\n%{http_code}",
        "--", checksum_url,
    ]
    try:
        result = subprocess.run(args, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GeoSearchError("maps_search_checksum_unavailable") from exc
    if result.returncode != 0:
        raise GeoSearchError("maps_search_checksum_unavailable")
    try:
        body, status = result.stdout.rsplit(b"\n", 1)
    except ValueError as exc:
        raise GeoSearchError("maps_search_checksum_unavailable") from exc
    if status != b"200" or len(body) > 256:
        raise GeoSearchError("maps_search_checksum_unavailable")
    return body


def _official_md5(opener, url):
    request = urllib.request.Request(url + ".md5",
                                     headers={"User-Agent": "PHAROS/0.7 (offline-search)"})
    try:
        with opener.open(request, timeout=35) as response:
            if response.status != 200:
                raise GeoSearchError("maps_search_checksum_unavailable")
            return _parse_publisher_md5(response.read(257), url)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise GeoSearchError("maps_search_checksum_unavailable") from exc
        # A Geofabrik HTTP 308 to a duplicated subdirectory ends at 404
        # under urllib, while curl obtains the genuine 200/58-byte checksum.
        # Never fall back to a guessed digest or unverified source.
        try:
            return _parse_publisher_md5(_curl_publisher_md5(url), url)
        except GeoSearchError as fallback:
            raise GeoSearchError("maps_search_checksum_unavailable") from fallback
    except (OSError, urllib.error.URLError, ValueError) as exc:
        if isinstance(exc, GeoSearchError):
            raise
        raise GeoSearchError("maps_search_checksum_unavailable") from exc


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        while (block := stream.read(4 * 1024 * 1024)):
            digest.update(block)
    return digest.hexdigest()


def fetch_osm_source(config, area_id, url, *, cancelled, progress):
    """A resumable, bounded download using an official HTTPS .md5 checksum.

    .md5 confirms a completed transfer against the publisher's checksum;
    it is NOT a cryptographic signature. A changed publisher snapshot
    invalidates any pre-existing partial file before resumption.
    """
    _area_id(area_id)
    if valid_source_url(url) is None:
        raise GeoSearchError("maps_search_source_unavailable")
    if not check_data_volume(config.storage).available:
        raise GeoSearchError("data_volume_unavailable")
    directory = _source_directory(config)
    _safe_directory(directory)
    target = directory / (area_id + ".osm.pbf")
    part = directory / ("." + area_id + ".part")
    checksum_sidecar = directory / ("." + area_id + ".md5")
    if any(p.is_symlink() for p in (target, part, checksum_sidecar)):
        raise GeoSearchError("maps_search_storage_unsafe")
    snapshot_url = _snapshot_source_url(url)
    opener = _opener()
    expected = _official_md5(opener, snapshot_url)
    _abort_if(cancelled)
    if target.is_file():
        if target.stat().st_size <= MAX_SOURCE_BYTES and _md5(target) == expected:
            return target
        target.unlink()
    # Only resume bytes from a snapshot with the same official MD5.
    try:
        old_checksum = checksum_sidecar.read_text(encoding="ascii").strip()
    except OSError:
        old_checksum = None
    if old_checksum != expected:
        part.unlink(missing_ok=True)
        checksum_sidecar.write_text(expected, encoding="ascii")
    start = part.stat().st_size if part.is_file() else 0
    if start > MAX_SOURCE_BYTES:
        raise GeoSearchError("maps_search_source_too_large")
    headers = {"User-Agent": "PHAROS/0.7 (offline-search)"}
    if start:
        headers["Range"] = "bytes=" + str(start) + "-"
    request = urllib.request.Request(snapshot_url, headers=headers)
    try:
        response = opener.open(request, timeout=90)
        with response:
            status = response.status
            if status == 206:
                range_header = response.headers.get("Content-Range", "")
                if not range_header.startswith("bytes " + str(start) + "-"):
                    raise GeoSearchError("maps_search_download_failed")
                mode = "ab"
                offset = start
            elif status == 200:
                mode = "wb"
                offset = 0
            else:
                raise GeoSearchError("maps_search_download_failed")
            announced = response.headers.get("Content-Length")
            announced_total = int(announced) + offset if announced and announced.isdigit() else None
            if announced_total is not None and announced_total > MAX_SOURCE_BYTES:
                raise GeoSearchError("maps_search_source_too_large")
            with part.open(mode) as stream:
                done = offset
                while True:
                    _abort_if(cancelled)
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_SOURCE_BYTES:
                        raise GeoSearchError("maps_search_source_too_large")
                    stream.write(chunk)
                    progress(done, announced_total)
        _abort_if(cancelled)
        if not part.is_file() or not part.stat().st_size or _md5(part) != expected:
            raise GeoSearchError("maps_search_checksum_mismatch")
        os.replace(part, target)
        checksum_sidecar.unlink(missing_ok=True)
        return target
    except GeoSearchError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise GeoSearchError("maps_search_download_failed") from exc



IMPORT_BATCH = 5000
INDEX_BATCH = 50000
# The incomplete candidate database and node-location cache both live under
# database_dir. Check that filesystem itself, not NAS data_dir.
MIN_FREE_BYTES = 256 * 1024 * 1024
MAX_RESERVE_BYTES = 2 * 1024 * 1024 * 1024


def _check_import_space(root: Path) -> None:
    """Bound index growth by actual local disk capacity, not an arbitrary row cap."""
    try:
        usage = shutil.disk_usage(root)
        reserve = min(MAX_RESERVE_BYTES, max(MIN_FREE_BYTES, usage.total // 20))
        if usage.free < reserve:
            raise GeoSearchError("maps_search_disk_space_low")
    except OSError as exc:
        raise GeoSearchError("maps_search_disk_space_low") from exc


def _node_location_index(source: Path, work: Path) -> str:
    """Use memory only for small extracts with demonstrable spare headroom.

    For regional/large extracts (including Nord-Est) retain file-backed node
    locations: the measured 4-GiB VM must not risk Linux OOM just to improve
    import throughput. SQLite optimizations apply to either mode.
    """
    try:
        source_size = source.stat().st_size
    except OSError:
        source_size = 1 << 60
    if source_size <= 96 * 1024 * 1024:
        # On Linux verify MemAvailable, on other platforms default to disk.
        try:
            data = Path("/proc/meminfo").read_text(encoding="ascii")
            match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", data, re.M)
            if match and int(match.group(1)) * 1024 >= 2 * 1024 ** 3:
                return "flex_mem"
        except OSError:
            pass
    return "sparse_file_array," + str(work / "nodes.cache")


def _publish_fts_rtree(conn: sqlite3.Connection, count: int, *, cancelled, report,
                       disk_root: Path) -> None:
    """Build both lookup indexes in SQL in bounded, cancellable rowid ranges."""
    _abort_if(cancelled)
    conn.execute("""CREATE VIRTUAL TABLE entries_fts USING fts5(
        name, detail, searchable, content='entries', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    )""")
    conn.commit()
    last = 0
    while last < count:
        _abort_if(cancelled)
        upper = min(count, last + INDEX_BATCH)
        conn.execute("""INSERT INTO entries_fts(rowid, name, detail, searchable)
                        SELECT id,name,detail,searchable FROM entries
                        WHERE id > ? AND id <= ? ORDER BY id""", (last, upper))
        conn.commit()
        last = upper
        _check_import_space(disk_root)
        report("maps_search_building_fts", last, count)
    _abort_if(cancelled)
    conn.execute("""CREATE VIRTUAL TABLE entries_rtree USING rtree(
        id, min_lat, max_lat, min_lon, max_lon
    )""")
    conn.commit()
    last = 0
    while last < count:
        _abort_if(cancelled)
        upper = min(count, last + INDEX_BATCH)
        conn.execute("""INSERT INTO entries_rtree(id,min_lat,max_lat,min_lon,max_lon)
                        SELECT id,latitude,latitude,longitude,longitude
                        FROM entries WHERE id > ? AND id <= ? ORDER BY id""",
                     (last, upper))
        conn.commit()
        last = upper
        _check_import_space(disk_root)
        report("maps_search_building_rtree", last, count)


# Index any reasonably-sized OSM key/value, rather than a hardcoded list
# of business types. Discard only non-query metadata and contact fields
# from the *search index*; their original short values remain in tags_json.
TAG_KEY = re.compile(r"[a-z][a-z0-9_:-]{0,79}\Z")
SKIP_TAG_PREFIXES = ("name:", "addr:", "source:", "note:", "fixme:",
                     "contact:", "description:")
SKIP_TAG_KEYS = {"name", "source", "note", "fixme", "description",
                 "website", "phone", "email", "wikipedia", "wikidata",
                 "image", "url", "created_by", "attribution"}
POI_CLASS_KEYS = ("amenity", "tourism", "shop", "healthcare", "leisure",
                  "historic", "railway", "office", "craft", "sport",
                  "natural", "man_made", "public_transport", "emergency",
                  "aeroway", "waterway", "landuse", "power", "information")
MAX_STORED_TAGS = 96
MAX_TAG_VALUE = 256


def _normalized_osm_tags(tags) -> dict[str, str]:
    """Bounded snapshot of original OSM attributes for each selected POI."""
    result = {}
    for tag in tags:
        # PyOsmium exposes Tag objects; regression fakes and ordinary
        # mappings iterate their string keys.
        if isinstance(tag, str):
            key = tag.strip().casefold()
            value = str(tags.get(tag) or "").strip()
        else:
            key = tag.k.strip().casefold()
            value = tag.v.strip()
        if (len(result) >= MAX_STORED_TAGS or not TAG_KEY.fullmatch(key)
                or not value or len(value) > MAX_TAG_VALUE):
            continue
        result[key] = value
    return result


def _search_tag_rows(entry_id: int, tags: dict[str, str]):
    """Searchable attributes without fixed category/feature lists.

    Semicolon-separated multi-values (OSM cuisine=italian;pizza) are
    searchable individually and as the complete raw value.
    """
    seen = set()
    for key, raw in tags.items():
        if key in SKIP_TAG_KEYS or key.startswith(SKIP_TAG_PREFIXES):
            continue
        candidates = [raw.casefold()]
        if ";" in raw:
            candidates += [part.strip().casefold() for part in raw.split(";")[:12]]
        for value in candidates:
            if not value or len(value) > 120 or (key, value) in seen:
                continue
            seen.add((key, value))
            yield (entry_id, key, value)


def build_index(config, area_id, bounds, source: Path, *, cancelled, progress,
                phase_progress=None):
    """Stream selective OSM entries; bulk build FTS5/RTree; atomically publish.

    The original .osm.pbf is never modified. The complete candidate DB and
    node cache are temporary and never visible to search until verified.
    phase_progress(stage, done, total) is optional: legacy progress(count)
    callers retain their existing contract.
    """
    _area_id(area_id)
    if (not isinstance(bounds, list) or len(bounds) != 4 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds) or
            not (-180 <= bounds[0] < bounds[2] <= 180 and
                 -90 <= bounds[1] < bounds[3] <= 90)):
        raise GeoSearchError("maps_search_area_invalid")
    try:
        import osmium
    except ImportError as exc:
        raise GeoSearchError("maps_search_parser_missing") from exc
    root = index_directory(config)
    _safe_directory(root)
    destination = index_file(config, area_id)
    if destination.is_symlink():
        raise GeoSearchError("maps_search_storage_unsafe")
    _check_import_space(root)

    def report(stage, done, total=None):
        if phase_progress is not None:
            phase_progress(stage, done, total)
        else:
            progress(done)

    _abort_if(cancelled)
    with tempfile.TemporaryDirectory(prefix="." + area_id + "-", dir=root) as workdir:
        work = Path(workdir)
        temporary = work / "index.sqlite3"
        conn = sqlite3.connect(temporary, timeout=30)
        conn.execute("PRAGMA cache_size=-32768")  # bounded ~32 MiB
        conn.execute("PRAGMA temp_store=FILE")
        # Only an unpublished, throwaway database is affected. A failed job
        # discards this DB, never changes the existing published generation.
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        # Interrupt long SQLite virtual-table builds when the job is cancelled.
        conn.set_progress_handler(lambda: int(bool(cancelled())), 5000)
        try:
            conn.executescript("""CREATE TABLE entries (
                id INTEGER PRIMARY KEY, osm_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, category TEXT NOT NULL,
                detail TEXT NOT NULL, latitude REAL NOT NULL,
                longitude REAL NOT NULL, searchable TEXT NOT NULL,
                poi_kind TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE entry_tags (
                entry_id INTEGER NOT NULL, tag_key TEXT NOT NULL,
                tag_value TEXT NOT NULL
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            insert = """INSERT INTO entries
                        (id,osm_id,name,category,detail,latitude,longitude,searchable,poi_kind,tags_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)"""
            class Collector(osmium.SimpleHandler):
                def __init__(self):
                    super().__init__()
                    self.count = 0
                    self.visited = 0
                    self.buffer = []
                    self.tag_buffer = []
                    self.tag_count = 0

                def flush(self):
                    if not self.buffer:
                        return
                    _abort_if(cancelled)
                    conn.executemany(insert, self.buffer)
                    if self.tag_buffer:
                        conn.executemany(
                            "INSERT INTO entry_tags(entry_id,tag_key,tag_value) "
                            "VALUES (?,?,?)", self.tag_buffer)
                    conn.commit()
                    self.buffer.clear()
                    self.tag_buffer.clear()
                    _check_import_space(root)
                    report("maps_search_importing", self.count)

                def tick(self):
                    self.visited += 1
                    if self.visited % 16384 == 0:
                        _abort_if(cancelled)
                        if not self.buffer:
                            report("maps_search_importing", self.count)

                def add(self, obj, latitude, longitude, typ):
                    if (not math.isfinite(latitude) or not math.isfinite(longitude) or
                            not (bounds[0] <= longitude <= bounds[2] and
                                 bounds[1] <= latitude <= bounds[3])):
                        return
                    tags = obj.tags
                    street = (tags.get("addr:street") or "").strip()
                    number = (tags.get("addr:housenumber") or "").strip()
                    original_name = (tags.get("name") or "").strip()
                    name = original_name
                    if name and tags.get("place"):
                        category = "place"
                    elif any(tags.get(k) for k in POI_CLASS_KEYS):
                        category = "poi"
                        if not name:
                            # Explicit label for an unnamed OSM feature;
                            # the coordinates belong to the actual object,
                            # not a geocoded guess.
                            for key in POI_CLASS_KEYS:
                                if tags.get(key):
                                    name = key + "=" + str(tags.get(key))[:90]
                                    break
                    elif street and number:
                        category = "address"
                        name = (street + " " + number)[:240]
                    elif typ == "w" and tags.get("highway") and name:
                        category = "street"
                    else:
                        return
                    if not name or len(name) > 240:
                        return
                    detail = ", ".join(x for x in (
                        (tags.get("addr:city") or "")[:100],
                        (tags.get("addr:postcode") or "")[:24]
                    ) if x)
                    aliases = " ".join(x for x in (
                        tags.get("name:it") or "", tags.get("name:en") or "",
                        tags.get("alt_name") or "", original_name, street, number
                    ) if x)[:500]
                    # Preserve the official OSM feature classification, not
                    # only its human-readable name. A museum called "MAR"
                    # must be discoverable via tourism=museum even without
                    # the word "museo" in the display name.
                    poi_kind = ""
                    if category == "poi":
                        for tag in ("tourism", "amenity", "healthcare", "leisure",
                                    "historic", "shop", "railway", "office"):
                            value = (tags.get(tag) or "").strip().casefold()
                            if re.fullmatch(r"[a-z0-9_:-]{1,60}", value):
                                poi_kind = tag + ":" + value
                                break
                    # Primitive values only: osmium views become invalid once
                    # their callback returns; never hold live objects in buffer.
                    index_tags = _normalized_osm_tags(tags) if category == "poi" else {}
                    self.count += 1
                    ident = self.count
                    self.buffer.append(
                        (ident, typ + str(obj.id), name, category, detail,
                         latitude, longitude, aliases, poi_kind,
                         json.dumps(index_tags, ensure_ascii=False,
                                    separators=(",", ":"))))
                    if index_tags:
                        new_tags = list(_search_tag_rows(ident, index_tags))
                        self.tag_buffer.extend(new_tags)
                        self.tag_count += len(new_tags)
                    if len(self.buffer) >= IMPORT_BATCH:
                        self.flush()

                def node(self, obj):
                    self.tick()
                    if obj.tags and obj.location.valid():
                        self.add(obj, obj.location.lat, obj.location.lon, "n")

                def way(self, obj):
                    self.tick()
                    if not obj.tags or len(obj.nodes) > 2048:
                        return
                    # Avoid costly coordinate lookups for roads/buildings
                    # which have no searchable tags.
                    tags = obj.tags
                    if not (tags.get("name") or
                            (tags.get("addr:street") and tags.get("addr:housenumber"))
                            or any(tags.get(k) for k in POI_CLASS_KEYS)):
                        return
                    count = 0
                    lat_sum = lon_sum = 0.0
                    for n in obj.nodes:
                        if n.location.valid():
                            lat_sum += n.location.lat
                            lon_sum += n.location.lon
                            count += 1
                    if count:
                        self.add(obj, lat_sum/count, lon_sum/count, "w")
            handler = Collector()
            report("maps_search_importing", 0)
            handler.apply_file(str(source), locations=True,
                               idx=_node_location_index(source, work))
            handler.flush()
            _abort_if(cancelled)
            count = handler.count
            report("maps_search_building_fts", 0, count)
            _publish_fts_rtree(conn, count, cancelled=cancelled,
                               report=report, disk_root=root)
            _abort_if(cancelled)
            report("maps_search_building_tags", 0, handler.tag_count)
            # The index is built AFTER the streaming import. This avoids
            # maintaining a sorted B-tree for each incoming OSM tag.
            conn.execute(
                "CREATE INDEX entry_tags_lookup ON "
                "entry_tags(tag_key,tag_value,entry_id)")
            conn.commit()
            _check_import_space(root)
            report("maps_search_building_tags", handler.tag_count,
                   handler.tag_count)
            _abort_if(cancelled)
            report("maps_search_verifying", count, count)
            conn.executemany("INSERT INTO metadata(key,value) VALUES (?,?)", [
                ("schema", INDEX_SCHEMA), ("area", area_id),
                ("count", str(count)), ("created_at", str(int(time.time()))),
                ("source", "OpenStreetMap / Geofabrik"),
            ])
            conn.commit()
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise GeoSearchError("maps_search_index_invalid")
            rowcount = conn.execute("SELECT count(*) FROM entries").fetchone()[0]
            if rowcount != count or conn.execute(
                    "SELECT count(*) FROM entries_rtree").fetchone()[0] != count:
                raise GeoSearchError("maps_search_index_invalid")
            invalid_tag = conn.execute(
                "SELECT 1 FROM entry_tags t LEFT JOIN entries e "
                "ON t.entry_id=e.id WHERE e.id IS NULL LIMIT 1").fetchone()
            if invalid_tag:
                raise GeoSearchError("maps_search_index_invalid")
            # FTS5 integrity command also checks indexed content against the
            # backing table, catching partial/incorrect bulk writes.
            conn.execute("""INSERT INTO entries_fts(entries_fts,rank)
                            VALUES('integrity-check',1)""")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if cancelled():
                raise GeoSearchError("maps_search_cancelled") from exc
            raise
        finally:
            conn.set_progress_handler(None, 0)
            conn.close()
        _abort_if(cancelled)
        # Flush the completed SQLite database before publishing atomically.
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    return {"area": area_id, "count": count}

def _read_metadata(path):
    if path.is_symlink() or not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key,value FROM metadata"))
            version = meta.get("schema")
            if version not in COMPATIBLE_INDEX_SCHEMAS:
                return None
            return {"area": meta["area"], "count": int(meta["count"]),
                    "created_at": int(meta["created_at"]),
                    "schema": version, "category_ready": version in ("2", "3"),
                    "tags_ready": version == "3"}
        finally:
            conn.close()
    except (sqlite3.Error, KeyError, ValueError, OSError):
        return None


def list_indexes(config):
    base = index_directory(config)
    if base.is_symlink() or not base.is_dir():
        return {"items": []}
    items = []
    for path in sorted(base.glob("*.sqlite3"))[:100]:
        if not AREA_ID.fullmatch(path.stem):
            continue
        meta = _read_metadata(path)
        if meta is not None and meta["area"] == path.stem:
            items.append(meta)
    return {"items": items}


def search(config, query, limit=20, *, latitude=None, longitude=None, radius_km=None):
    """Use existing FTS5/RTree to disambiguate streets by nearby place name.

    No reindex or additional source download is needed: settlement entries
    already have representative coordinates in the SQLite generation.
    """
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120:
        raise GeoSearchError("maps_search_query_invalid")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise GeoSearchError("maps_search_limit_invalid")
    if (latitude is None) != (longitude is None) or (
            radius_km is not None and latitude is None):
        raise GeoSearchError("maps_search_location_invalid")
    if latitude is not None and (
            not all(type(v) in (int,float) and math.isfinite(v)
                    for v in (latitude, longitude, radius_km)) or
            not -90 <= latitude <= 90 or not -180 <= longitude <= 180
            or not 0.1 <= radius_km <= 200):
        raise GeoSearchError("maps_search_location_invalid")
    if not re.search(r"[^\W_]", query):
        return {"items": [], "indexes": 0}
    found, indexed = {}, 0
    for meta in list_indexes(config)["items"]:
        area = meta["area"]
        path = index_file(config, area)
        try:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                candidates = search_index(
                    db, query, limit=limit, latitude=latitude,
                    longitude=longitude, radius_km=radius_km)
                indexed += 1
                for item in candidates:
                    item["area"] = area
                    key = item["osm_id"]
                    if key not in found or found[key]["rank"] < item["rank"]:
                        found[key] = item
            finally:
                db.close()
        except (sqlite3.Error, OSError):
            # Continue searching other installed geographical indexes.
            continue
    ordered = sorted(found.values(), key=lambda item: (
        -item["rank"], item.get("distance_km", float("inf")),
        item["name"].casefold()))
    ordered = collapse_segments(ordered, limit)
    for item in ordered:
        item.pop("id", None)
        item.pop("searchable", None)
        item.pop("poi_kind", None)
        item.pop("tags_json", None)
        item.pop("rank", None)
    return {"items": ordered, "indexes": indexed}


def search_category(config, category: str, locality: str, limit: int = 20):
    """Locate POI types by OSM tag near an indexed locality (schema v2)."""
    from .maps_categories import find_category
    try:
        return find_category(config, category, locality, limit,
                             indexes=list_indexes(config)["items"],
                             index_path=lambda area: index_file(config, area))
    except ValueError as exc:
        raise GeoSearchError(str(exc)) from exc


def search_tag_clauses(config, clauses, locality, limit=20):
    """Generic v3 POI lookup; all tag predicates are verified in SQLite."""
    from .maps_tags import search_tags
    try:
        return search_tags(config, clauses, locality, limit=limit,
                           indexes=list_indexes(config)["items"],
                           index_path=lambda area: index_file(config, area))
    except ValueError as exc:
        raise GeoSearchError(str(exc)) from exc


def indexed_tag_keys(config):
    from .maps_tags import catalog_keys
    return catalog_keys(config, indexes=list_indexes(config)["items"],
                        index_path=lambda area: index_file(config, area))


def delete_index(config, area_id: str, jobs):
    """Explicitly remove ONLY the chosen derived SQLite index, never PMTiles."""
    _area_id(area_id)
    path = index_file(config, area_id)
    if index_directory(config).is_symlink() or path.is_symlink():
        raise GeoSearchError("maps_search_storage_unsafe")
    with jobs._lock, jobs._db() as db:
        rows = db.execute("""SELECT kind,params FROM jobs WHERE
            kind IN ('download-map','index-map') AND status IN ('queued','running')""").fetchall()
        for row in rows:
            try:
                params = json.loads(row["params"])
            except (TypeError, ValueError):
                raise GeoSearchError("maps_search_delete_busy")
            if (params.get("region") == area_id and
                    (row["kind"] == "index-map" or params.get("include_search"))):
                raise GeoSearchError("maps_search_delete_busy")
        if not path.is_file():
            raise GeoSearchError("maps_search_not_found")
        try:
            path.unlink()
        except OSError as exc:
            raise GeoSearchError("maps_search_delete_failed") from exc
    return {"deleted": area_id}


def search_named_locality(config, query, locality, limit=5):
    """Conservative named lookup: exact locality anchor and full name tokens.

    No broad regional fallback when a locality is explicitly supplied.
    """
    from .maps_geosearch import _words, _places_named, _distance_km, CITY_RADIUS_KM
    subject = _words(query)
    town = _words(locality)
    if not subject or len(query) > 120 or (locality and not town):
        raise GeoSearchError("maps_search_query_invalid")
    if not locality:
        # Standalone names must match exactly, rather than arbitrary FTS details.
        rows = search(config, query, min(50, max(limit, 20)))["items"]
        return {"items": [r for r in rows if _words(r["name"]) == subject][:limit],
                "locality_found": True}
    results = {}
    resolved = False
    for meta in list_indexes(config)["items"]:
        area = meta["area"]
        try:
            db = sqlite3.connect(index_file(config, area).resolve().as_uri() +
                                 "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                anchors = _places_named(db, town)
                if not anchors:
                    continue
                resolved = True
                # Resolve the full subject independently of locality parsing.
                for anchor in anchors:
                    lat, lon = anchor["latitude"], anchor["longitude"]
                    rows = search_index(db, query, limit=50, latitude=lat,
                                        longitude=lon, radius_km=CITY_RADIUS_KM,
                                        explicit_subject=True)
                    for row in rows:
                        name_words = _words(row["name"])
                        # Exact names, or the complete ordered subject in a
                        # named road/address. Do not accept broad POI prefixes.
                        exact = name_words == subject
                        road = row["category"] in ("street", "address")
                        if not exact and not (road and any(
                                name_words[i:i+len(subject)] == subject
                                for i in range(len(name_words)-len(subject)+1))):
                            continue
                        distance = _distance_km(lat, lon, row["latitude"], row["longitude"])
                        if distance > CITY_RADIUS_KM:
                            continue
                        row["area"] = area
                        row["locality_hint"] = anchor["name"]
                        row["locality_distance_km"] = round(distance, 3)
                        key = (area, row["osm_id"])
                        if key not in results or results[key]["locality_distance_km"] > distance:
                            results[key] = row
            finally:
                db.close()
        except (sqlite3.Error, OSError):
            continue
    ordered = sorted(results.values(), key=lambda r: (-r["rank"], r["locality_distance_km"]))
    return {"items": collapse_segments(ordered, limit), "locality_found": resolved}
