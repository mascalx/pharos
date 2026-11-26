"""Isolated offline maps foundation: PMTiles inventory and byte-range safety."""
import os
import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config


def test_empty_inventory_and_range_delivery(prepared):
    config = load_config(prepared)
    directory = config.storage.data_dir / "maps" / "regions"
    with TestClient(create_app(config)) as client:
        assert client.get("/api/maps/regions").json() == {"items": []}
        directory.mkdir(parents=True)
        payload = b"PMTiles" + bytes([3]) + bytes(120)
        (directory / "sample.pmtiles").write_bytes(payload)
        (directory / "bad.pmtiles").write_bytes(b"invalid")
        listed = client.get("/api/maps/regions")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == ["sample.pmtiles"]
        url = "/api/maps/regions/sample.pmtiles/archive"
        assert client.get(url).status_code == 416
        response = client.get(url, headers={"Range": "bytes=0-7"})
        assert response.status_code == 206
        assert response.content == b"PMTiles" + bytes([3])
        assert response.headers["content-range"] == f"bytes 0-7/{len(payload)}"
        assert client.get(url, headers={"Range": "bytes=8-"}).status_code == 206
        assert client.get(url, headers={"Range": "bytes=0-999999999"}).status_code == 206
        assert client.get(url, headers={"Range": "bytes=0-1,4-5"}).status_code == 416
        assert client.get("/api/maps/regions/../archive").status_code in (404, 422)


def test_maps_fail_closed_on_unavailable_volume(prepared):
    config = load_config(prepared)
    (config.storage.data_dir / ".pharos-volume").unlink()
    with TestClient(create_app(config)) as client:
        assert client.get("/api/maps/regions").status_code == 503
        assert client.get("/api/maps/regions/a.pmtiles/archive",
                          headers={"Range": "bytes=0-7"}).status_code == 503


def test_maps_reject_symlink_and_invalid_header(prepared):
    config = load_config(prepared)
    directory = config.storage.data_dir / "maps" / "regions"
    directory.mkdir(parents=True)
    (directory / "not-map.pmtiles").write_bytes(b"not a valid map")
    try:
        (directory / "linked.pmtiles").symlink_to(directory / "not-map.pmtiles")
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks requires additional Windows privileges")
    with TestClient(create_app(config)) as client:
        assert client.get("/api/maps/regions").json()["items"] == []
        assert client.get("/api/maps/regions/linked.pmtiles/archive",
                          headers={"Range": "bytes=0-7"}).status_code == 404
        assert client.get("/api/maps/regions/not-map.pmtiles/archive",
                          headers={"Range": "bytes=0-7"}).status_code == 422
