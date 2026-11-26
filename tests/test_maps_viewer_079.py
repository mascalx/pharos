"""Offline map viewer installation, bounded npm verification and PMTiles header tests."""
from __future__ import annotations

import io
import json
import tarfile
import hashlib
import base64

from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.maps import _viewer_header
from pharos import maps_viewer_assets as assets


def _map(path, tile_type=1, zoom=15):
    from struct import pack_into
    header = bytearray(127)
    header[:8] = b"PMTiles" + bytes([3])
    header[99], header[100], header[101] = tile_type, 0, zoom
    for offset, value in zip((102, 106, 110, 114),
                             (10.3, 43.7, 13.8, 46.1)):
        pack_into("<i", header, offset, round(value * 1e7))
    path.write_bytes(header)
    return path


def test_vector_archive_readonly_bounds_and_range(prepared):
    cfg = load_config(prepared)
    region_dir = cfg.storage.data_dir / "maps" / "regions"
    region_dir.mkdir(parents=True)
    archive = _map(region_dir / "nord-est-z15.pmtiles")
    original = archive.read_bytes()
    with TestClient(create_app(cfg)) as client:
        rows = client.get("/api/maps/regions").json()["items"]
        item = next(x for x in rows if x["id"] == "nord-est-z15.pmtiles")
        assert item["viewer"]["vector"] is True
        assert item["viewer"]["minzoom"] == 0
        assert item["viewer"]["maxzoom"] == 15
        assert item["viewer"]["bounds"] == [10.3, 43.7, 13.8, 46.1]
        response = client.get(item["archive_url"],
                              headers={"Range": "bytes=0-126"})
        assert response.status_code == 206
        assert response.content == original
        assert client.get(item["archive_url"]).status_code == 416
    assert archive.read_bytes() == original


def test_unknown_vector_header_does_not_claim_viewer(prepared):
    cfg = load_config(prepared)
    d = cfg.storage.data_dir / "maps" / "regions"
    d.mkdir(parents=True)
    _map(d / "raster.pmtiles", tile_type=2)
    with TestClient(create_app(cfg)) as client:
        rows=client.get("/api/maps/regions").json()["items"]
        assert rows[0]["viewer"]["vector"] is False



def test_maplibre_v6_official_esm_artifacts():
    # These are the published 6.16.0 npm tarball paths; there is no
    # dist/maplibre-gl.js UMD bundle in MapLibre 6.
    assert assets.PACKAGES["maplibre-gl"][0] == "6.16.0"
    assert assets.PACKAGES["maplibre-gl"][1] == {
        "dist/maplibre-gl.mjs": "maplibre-gl.mjs",
        "dist/maplibre-gl-worker.mjs": "maplibre-gl-worker.mjs",
        "dist/maplibre-gl.css": "maplibre-gl.css",
    }
    assert assets.PACKAGES["pmtiles"][1] == {"dist/pmtiles.js": "pmtiles.js"}
    assert "maplibre-gl.js" not in assets.ASSET_FILES


def test_viewer_package_install_atomic_and_assets_local(prepared, monkeypatch):
    cfg = load_config(prepared)
    assert assets.status(cfg)["installed"] is False
    def fake_metadata(name, version):
        return {"url": f"https://registry.npmjs.org/{name}/{version}",
                "integrity": "sha512-" + base64.b64encode(bytes(64)).decode()}
    def fake_download(url, integrity, target):
        name = "maplibre-gl" if "maplibre" in url else "pmtiles"
        files = assets.PACKAGES[name][1]
        with tarfile.open(target, "w:gz") as t:
            for source in files:
                data = (name + source).encode("utf-8")
                info = tarfile.TarInfo("package/" + source)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(assets, "_metadata", fake_metadata)
    monkeypatch.setattr(assets, "_download_package", fake_download)
    result = assets.install(cfg)
    assert result["installed"] is True
    with TestClient(create_app(cfg)) as client:
        status = client.get("/api/maps/viewer-assets/status")
        assert status.status_code == 200
        assert status.json()["installed"] is True
        bundle = client.get("/api/maps/viewer-assets/pmtiles.js")
        assert bundle.status_code == 200
        assert bundle.content == b"pmtilesdist/pmtiles.js"
        assert client.get("/api/maps/viewer-assets/unknown.js").status_code == 404
    assert assets.install(cfg)["installed"] is True


def test_corrupt_viewer_checksum_fails_closed(prepared):
    cfg = load_config(prepared)
    base = assets.asset_directory(cfg)
    base.mkdir(parents=True, exist_ok=True)
    checksums = {}
    for name in assets.ASSET_FILES:
        payload=b"example asset"
        (base / name).write_bytes(payload)
        checksums[name]=hashlib.sha256(payload).hexdigest()
    (base / assets.MANIFEST).write_text(json.dumps({
        "packages": {key: value[0] for key, value in assets.PACKAGES.items()},
        "sha256": checksums,
    }),encoding="utf-8")
    assert assets.ready(cfg)
    (base / "pmtiles.js").write_bytes(b"altered")
    assert not assets.ready(cfg)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/maps/viewer-assets/pmtiles.js").status_code == 503


def test_archive_read_only_check_and_range_diagnostics(prepared):
    from struct import pack_into
    cfg=load_config(prepared)
    root=cfg.storage.data_dir / "maps" / "regions"
    root.mkdir(parents=True,exist_ok=True)
    target=_map(root / "nord-est-z15.pmtiles")
    header=bytearray(target.read_bytes())
    header.extend(b"R")
    pack_into("<Q",header,8,127)  # rootDirectoryOffset
    pack_into("<Q",header,16,1)   # rootDirectoryLength
    target.write_bytes(header)
    before=target.read_bytes()
    with TestClient(create_app(cfg)) as client:
        check=client.get("/api/maps/regions/nord-est-z15.pmtiles/check")
        assert check.status_code==200
        assert check.json()["valid"] is True
        assert check.json()["header"]["vector"] is True
        wrong=client.get("/api/maps/regions/nord-est-z15.pmtiles/archive",
                         headers={"Range":"bytes=0-16383"})
        assert wrong.status_code==206
        assert wrong.headers["content-range"]=="bytes 0-127/128"
        assert wrong.content==before
    assert target.read_bytes()==before
    # Do not claim a structurally broken root directory is a valid archive.
    pack_into("<Q",header,16,100000)
    target.write_bytes(header)
    with TestClient(create_app(cfg)) as client:
        check=client.get("/api/maps/regions/nord-est-z15.pmtiles/check")
        assert check.status_code==200
        assert check.json()=={"valid":False,"reason":"maps_check_invalid_directory"}


def test_official_font_install_is_verified_and_served_locally(prepared, monkeypatch):
    cfg=load_config(prepared)
    payload=b"offline-font-fixture"
    git_sha=hashlib.sha1(
        b"blob "+str(len(payload)).encode()+b"\0"+payload).hexdigest()
    monkeypatch.setattr(assets,"FONT_BLOBS",{"0-255.pbf":git_sha})
    # The font must be obtained from the immutable official GitHub URL.
    seen=[]
    class Reply(io.BytesIO):
        status=200
    class Publisher:
        def open(self,request,timeout):
            seen.append(request.full_url)
            return Reply(payload)
    monkeypatch.setattr(assets,"_opener",lambda:Publisher())
    assert assets.install_fonts(cfg)
    assert assets.labels_ready(cfg)
    assert seen==["https://raw.githubusercontent.com/protomaps/basemaps-assets/"+
        assets.FONT_COMMIT+"/fonts/Noto%20Sans%20Regular/0-255.pbf"]
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/maps/viewer-assets/status").json()["labels_installed"]
        response=client.get("/api/maps/fonts/Noto%20Sans%20Regular/0-255.pbf")
        assert response.status_code==200 and response.content==payload
        assert client.get("/api/maps/fonts/Other/0-255.pbf").status_code==404
        assert client.get("/api/maps/fonts/Noto%20Sans%20Regular/256-511.pbf").status_code==404
    (assets.font_directory(cfg)/"0-255.pbf").write_bytes(b"tampered")
    assert not assets.labels_ready(cfg)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/maps/fonts/Noto%20Sans%20Regular/0-255.pbf").status_code==503


def test_font_blob_verification_rejects_untrusted_bytes(prepared,monkeypatch):
    cfg=load_config(prepared)
    monkeypatch.setattr(assets,"FONT_BLOBS",{"0-255.pbf":"0"*40})
    class Reply(io.BytesIO):
        status=200
    class Publisher:
        def open(self,request,timeout):
            return Reply(b"wrong-font-content")
    monkeypatch.setattr(assets,"_opener",lambda:Publisher())
    from pharos.maps_viewer_assets import MapViewerAssetError
    import pytest
    with pytest.raises(MapViewerAssetError,match="maps_font_checksum_mismatch"):
        assets.install_fonts(cfg)
    assert not assets.labels_ready(cfg)
