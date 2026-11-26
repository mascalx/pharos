"""PHAROS 0.7.0.dev12 — offline geographic search, OSM index safety.

No network, Qdrant, Tiiny, real publisher download or installed PMTiles
are used by these tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pharos import maps_areas
from pharos.maps_search import (
    GeoSearchError, build_index, index_file, list_indexes,
    search, search_category, valid_source_url,
)

SOURCE = "https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf"


def cfg(tmp_path):
    data = tmp_path / "data"
    db = tmp_path / "db"
    data.mkdir()
    db.mkdir()
    return SimpleNamespace(storage=SimpleNamespace(data_dir=data, database_dir=db))


def fixture_osm(tmp_path):
    source = tmp_path / "example.osm"
    source.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="pharos-test">
  <node id="1" version="1" lat="44.417" lon="12.199">
    <tag k="name" v="Ravenna"/><tag k="place" v="city"/>
  </node>
  <node id="2" version="1" lat="44.420" lon="12.200">
    <tag k="addr:street" v="Via Cavour"/>
    <tag k="addr:housenumber" v="15"/><tag k="addr:city" v="Ravenna"/>
  </node>
  <node id="3" version="1" lat="44.425" lon="12.201">
    <tag k="name" v="Museo dei Mosaici"/><tag k="tourism" v="museum"/>
  </node>
  <node id="4" version="1" lat="52.0" lon="13.0">
    <tag k="name" v="Berlino"/><tag k="place" v="city"/>
  </node>
  <node id="5" version="1" lat="44.416" lon="12.195"/>
  <node id="6" version="1" lat="44.419" lon="12.202"/>
  <way id="7" version="1">
    <nd ref="5"/><nd ref="6"/>
    <tag k="name" v="Via Roma"/><tag k="highway" v="residential"/>
  </way>
</osm>""", encoding="utf-8")
    return source


def test_publisher_url_validation_and_catalog_data():
    assert valid_source_url(SOURCE) == SOURCE
    for bad in [
        "http://download.geofabrik.de/europe/italy-latest.osm.pbf",
        "https://example.invalid/europe/italy-latest.osm.pbf",
        "https://download.geofabrik.de/../internal-latest.osm.pbf",
        "https://download.geofabrik.de/europe/italy-latest.osm.pbf?x=1",
        "https://127.0.0.1/europe/italy-latest.osm.pbf",
    ]:
        assert valid_source_url(bad) is None
    obj = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"id": "nord-est", "name": "Nord-Est", "parent": None,
                       "urls": {"pbf": SOURCE}},
        "geometry": {"type": "Polygon",
                     "coordinates": [[[10, 43], [14, 43], [14, 46],
                                      [10, 46], [10, 43]]]}
    }]}
    item = maps_areas.parse_catalog(obj)[0]
    assert item["pbf_url"] == SOURCE
    obj["features"][0]["properties"]["urls"]["pbf"] = "http://127.0.0.1/a"
    assert maps_areas.parse_catalog(obj)[0]["pbf_url"] is None


def test_cold_geofabrik_cache_retains_pbf_url(tmp_path, monkeypatch):
    path = tmp_path / "maps-geography.json"
    item = {"id": "nord-est", "parent": None, "name": "Nord-Est",
            "bbox": [10, 43, 14, 46], "zoom": 12,
            "downloadable": True, "pbf_url": SOURCE}
    monkeypatch.setattr(maps_areas, "_cache", {"expires": 0., "items": None})
    monkeypatch.setattr(maps_areas, "_fetch", lambda: [item])
    assert maps_areas.areas(cache_path=path)["status"] == "available"
    monkeypatch.setattr(maps_areas, "_cache", {"expires": 0., "items": None})
    monkeypatch.setattr(maps_areas, "_fetch", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert maps_areas.areas(cache_path=path)["items"][0]["pbf_url"] == SOURCE


def test_index_search_is_local_and_preserves_existing_generation(tmp_path):
    pytest.importorskip("osmium")
    config = cfg(tmp_path)
    source = fixture_osm(tmp_path)
    bounds = [11.9, 44.1, 12.4, 44.6]
    build_index(config, "nord-est", bounds, source,
                cancelled=lambda: False, progress=lambda count: None)
    assert list_indexes(config)["items"][0]["count"] >= 4
    city = search(config, "Ravenna")
    assert city["indexes"] == 1
    assert any(row["name"] == "Ravenna" and row["category"] == "place"
               for row in city["items"])
    address = search(config, "via cavour 15")
    assert any(row["category"] == "address" for row in address["items"])
    assert any(row["category"] == "poi" for row in search(config, "mosaici")["items"])
    assert list_indexes(config)["items"][0]["category_ready"] is True
    found = search_category(config, "museum", "Ravenna")
    assert any(item["name"] == "Museo dei Mosaici" for item in found["items"])
    assert any(row["category"] == "street" for row in search(config, "roma")["items"])
    assert not search(config, "Berlino")["items"]
    assert search(config, "museo", latitude=44.42, longitude=12.2,
                  radius_km=4)["items"]
    assert not search(config, "museo", latitude=40., longitude=12.,
                      radius_km=1)["items"]
    before = index_file(config, "nord-est").read_bytes()
    with pytest.raises(GeoSearchError, match="maps_search_cancelled"):
        build_index(config, "nord-est", bounds, source, cancelled=lambda: True,
                    progress=lambda count: None)
    assert index_file(config, "nord-est").read_bytes() == before
    assert search(config, "ravenna")["items"]


def test_search_inputs_are_bounded(tmp_path):
    config = cfg(tmp_path)
    with pytest.raises(GeoSearchError):
        search(config, "x")
    with pytest.raises(GeoSearchError):
        search(config, "ravenna", limit=200)
    with pytest.raises(GeoSearchError):
        search(config, "ravenna", latitude=44.0)
    with pytest.raises(GeoSearchError):
        index_file(config, "../escape")


def test_deleting_search_index_never_deletes_the_map(tmp_path):
    import sqlite3
    import threading
    from contextlib import contextmanager
    from pharos.maps_search import delete_index
    config = cfg(tmp_path)
    store = index_file(config, "nord-est")
    store.parent.mkdir(parents=True)
    store.write_bytes(b"derived index")
    pmtiles = config.storage.data_dir / "maps" / "regions" / "nord-est.pmtiles"
    pmtiles.parent.mkdir(parents=True)
    pmtiles.write_bytes(b"original map must survive")
    class EmptyJobs:
        _lock = threading.RLock()
        @contextmanager
        def _db(self):
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE jobs (kind TEXT,params TEXT,status TEXT)")
            try:
                yield db
            finally:
                db.close()
    assert delete_index(config, "nord-est", EmptyJobs()) == {"deleted":"nord-est"}
    assert not store.exists()
    assert pmtiles.read_bytes() == b"original map must survive"


def test_dated_geofabrik_redirect_matches_exact_region_without_downloading():
    """Geofabrik redirects latest to dated snapshots; the MD5 is dated too."""
    from urllib.error import HTTPError
    from pharos.maps_search import _snapshot_source_url

    class Redirecting:
        def __init__(self, steps):
            self.steps = list(steps)
            self.calls = []

        def open(self, request, timeout=None):
            self.calls.append((request.get_method(), request.full_url))
            code, location = self.steps.pop(0)
            raise HTTPError(request.full_url, code, "redirect",
                            {"Location": location} if location else {}, None)

    dated = ("https://download.geofabrik.de/europe/italy/nord-est/"
             "nord-est-261119.osm.pbf")
    probe = Redirecting([(302, dated)])
    assert _snapshot_source_url(SOURCE, opener=probe) == dated
    assert probe.calls == [("HEAD", SOURCE)]

    # A hop to the region directory is allowed, but not to another area.
    nested = ("https://download.geofabrik.de/europe/italy/nord-est/"
              "nord-est-latest.osm.pbf")
    probe = Redirecting([(302, nested), (302, dated)])
    assert _snapshot_source_url(SOURCE, opener=probe) == dated
    assert probe.calls == [("HEAD", SOURCE), ("HEAD", nested)]


@pytest.mark.parametrize("unsafe", [
    "https://127.0.0.1/europe/italy/nord-est/nord-est-261119.osm.pbf",
    "https://example.com/europe/italy/nord-est/nord-est-261119.osm.pbf",
    "http://download.geofabrik.de/europe/italy/nord-est/nord-est-261119.osm.pbf",
    "https://download.geofabrik.de/europe/italy/centro/centro-261119.osm.pbf",
    "https://download.geofabrik.de/europe/italy/nord-est/nord-est-261119.osm.pbf?key=x",
    "https://download.geofabrik.de/europe/italy/nord-est/nord-est-261119.osm.pbf#f",
    "https://download.geofabrik.de/europe/italy/nord-est/nord-est-261119.osm.pbf/../evil",
])
def test_geofabrik_latest_redirect_rejects_unexpected_destinations(unsafe):
    from urllib.error import HTTPError
    from pharos.maps_search import _snapshot_source_url
    class Redirect:
        def open(self, request, timeout=None):
            raise HTTPError(request.full_url, 302, "redirect",
                            {"Location": unsafe}, None)
    with pytest.raises(GeoSearchError, match="maps_search_snapshot_unavailable"):
        _snapshot_source_url(SOURCE, opener=Redirect())


def test_search_download_uses_dated_file_and_matching_checksum(tmp_path, monkeypatch):
    """Fully fake HTTPS: confirm no request to latest.md5 and no unverified file."""
    import hashlib
    from io import BytesIO
    from types import SimpleNamespace
    from pharos import maps_search

    config = cfg(tmp_path)
    dated = ("https://download.geofabrik.de/europe/italy/nord-est/"
             "nord-est-261119.osm.pbf")
    payload = b"test-osm-bytes-streamed-to-disk"
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    calls = []

    class Response(BytesIO):
        def __init__(self, body):
            super().__init__(body)
            self.status = 200
            self.headers = {"Content-Length": str(len(body))}

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request.full_url)
            assert request.full_url in (dated, dated + ".md5")
            if request.full_url.endswith(".md5"):
                return Response((digest + "  nord-est-261119.osm.pbf\n").encode("ascii"))
            return Response(payload)

    monkeypatch.setattr(maps_search, "_snapshot_source_url", lambda value: dated)
    monkeypatch.setattr(maps_search, "_opener", lambda: Opener())
    monkeypatch.setattr(maps_search, "check_data_volume",
                        lambda storage: SimpleNamespace(available=True))
    completed = maps_search.fetch_osm_source(
        config, "nord-est", SOURCE,
        cancelled=lambda: False, progress=lambda done, total: None)
    assert completed.read_bytes() == payload
    assert calls == [dated + ".md5", dated]
    # A retry of this validated file skips the large download.
    completed2 = maps_search.fetch_osm_source(
        config, "nord-est", SOURCE,
        cancelled=lambda: False, progress=lambda done, total: None)
    assert completed2 == completed
    assert calls == [dated + ".md5", dated, dated + ".md5"]


def test_dated_geofabrik_redirect_get_fallback_when_head_unsupported():
    from urllib.error import HTTPError
    from pharos.maps_search import _snapshot_source_url
    dated = ("https://download.geofabrik.de/europe/italy/nord-est/"
             "nord-est-261119.osm.pbf")
    calls = []
    class RejectsHead:
        def open(self, request, timeout=None):
            calls.append((request.get_method(),request.get_header("Range")))
            if request.get_method() == "HEAD":
                raise HTTPError(request.full_url, 405, "method not allowed", {}, None)
            raise HTTPError(request.full_url, 302, "found", {"Location": dated}, None)
    assert _snapshot_source_url(SOURCE, opener=RejectsHead()) == dated
    assert calls == [("HEAD", None), ("GET", "bytes=0-0")]


def test_geofabrik_md5_308_duplicate_path_404_uses_bounded_curl(monkeypatch):
    """The real VM reached 404 via urllib 308; curl received valid HTTP 200."""
    from urllib.error import HTTPError
    from types import SimpleNamespace
    from pharos import maps_search

    dated = ("https://download.geofabrik.de/europe/italy/nord-est/"
             "nord-est-261119.osm.pbf")
    hex_digest = "2a57852c8b4a660f8dae1ecc12034ad9"
    calls = []

    class BrokenPythonRedirect:
        def open(self, req, timeout=None):
            calls.append(("urllib", req.full_url))
            raise HTTPError(
                "https://download.geofabrik.de/europe/italy/"
                "nord-est/nord-est/nord-est-261119.osm.pbf.md5",
                404, "Not Found", {}, None)

    def fake_run(args, **kwargs):
        calls.append(("curl", args))
        assert kwargs["capture_output"]
        assert kwargs["timeout"] <= 35
        assert "--location" not in args
        assert "--proto" in args
        assert args[-1] == dated + ".md5"
        return SimpleNamespace(returncode=0,
            stdout=(hex_digest+"  nord-est-261119.osm.pbf\n200").encode("ascii"))

    monkeypatch.setattr(maps_search.subprocess, "run", fake_run)
    result = maps_search._official_md5(BrokenPythonRedirect(), dated)
    assert result == hex_digest
    assert len(calls) == 2


def test_geofabrik_md5_fallback_fail_closed(monkeypatch):
    from urllib.error import HTTPError
    from types import SimpleNamespace
    from pharos import maps_search

    dated = ("https://download.geofabrik.de/europe/italy/nord-est/"
             "nord-est-261119.osm.pbf")
    class BrokenPythonRedirect:
        def open(self, req, timeout=None):
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)

    for stdout, exitcode in [
        (b"Not Found\n404", 0),
        (b"<html>redirect</html>\n200", 0),
        (b"2a57852c8b4a660f8dae1ecc12034ad9  other-file.osm.pbf\n200", 0),
        (b"2a57852c8b4a660f8dae1ecc12034ad9  nord-est-261119.osm.pbf\n200", 22),
    ]:
        monkeypatch.setattr(maps_search.subprocess, "run", lambda *a, **k:
                            SimpleNamespace(returncode=exitcode, stdout=stdout))
        with pytest.raises(GeoSearchError, match="maps_search_checksum_unavailable"):
            maps_search._official_md5(BrokenPythonRedirect(), dated)


def test_geofabrik_md5_fallback_refuses_other_hosts_and_snapshots():
    from pharos.maps_search import _curl_publisher_md5
    for url in (
        "http://download.geofabrik.de/europe/italy/nord-est-261119.osm.pbf",
        "https://example.com/europe/italy/nord-est-261119.osm.pbf",
        "https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf",
        "https://download.geofabrik.de/europe/italy/nord-est-261119.osm.pbf?token=x",
        "https://download.geofabrik.de/europe/italy/../../private-261119.osm.pbf",
    ):
        with pytest.raises(GeoSearchError, match="maps_search_checksum_unavailable"):
            _curl_publisher_md5(url)
