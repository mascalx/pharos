"""Official build listing checks; no real network in unit tests."""
from __future__ import annotations
import io
from pharos import maps_catalog


class _Fake:
    status = 206
    headers = {"Content-Range": "bytes 0-7/2048"}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size):
        return b"PMTiles" + bytes([3])


def test_verified_source_catalog_and_cache(monkeypatch):
    monkeypatch.setattr(maps_catalog, "_listing", lambda: ["20261116.pmtiles", "20261115.pmtiles"])
    monkeypatch.setattr(maps_catalog, "_probe", lambda name: name == "20261116.pmtiles")
    data = maps_catalog.builds(refresh=True)
    assert data["status"] == "available"
    assert [x["id"] for x in data["items"]] == ["20261116.pmtiles"]
    assert maps_catalog.validate_source("20261116.pmtiles") == "https://build.protomaps.com/20261116.pmtiles"
    try:
        maps_catalog.validate_source("http://127.0.0.1/x.pmtiles")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe sources must be refused")


def test_listing_parses_only_official_build_names(monkeypatch):
    class Page:
        status = 200
        headers = {"Content-Length": "95"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            return b'<a href="20261116.pmtiles">20261116.pmtiles</a><a>20261115.pmtiles</a>'
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == maps_catalog.INDEX_URL
            return Page()
    monkeypatch.setattr(maps_catalog, "_opener", lambda: Opener())
    assert maps_catalog._listing() == ["20261116.pmtiles", "20261115.pmtiles"]


def test_header_probe_must_confirm_range_and_pmtiles(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            assert request.headers["Range"] == "bytes=0-7"
            return _Fake()
    monkeypatch.setattr(maps_catalog, "_opener", lambda: Opener())
    assert maps_catalog._probe("20261116.pmtiles")
