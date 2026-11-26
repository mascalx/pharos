"""Map library deletion: explicit authorization, queue guard, data safety."""
from __future__ import annotations

import json
import time
import uuid

from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config


def _archive(root, name, *, sidecar=True):
    path = root / name
    path.write_bytes(b"PMTiles"+bytes([3])+bytes(119))
    if sidecar:
        (root / name.replace(".pmtiles",".json")).write_text(json.dumps({
            "schema":1,"id":name[:-8],"bbox":[6.0,35.0,19.0,48.0],
            "source":"20261117.pmtiles","requested_zoom":15
        }),encoding="utf-8")
    return path


def test_delete_requires_mutation_header_and_preserves_other_archives(prepared):
    cfg=load_config(prepared)
    directory=cfg.storage.data_dir/"maps"/"regions"
    directory.mkdir(parents=True)
    target=_archive(directory,"nord-est-z15.pmtiles")
    other=_archive(directory,"italy.pmtiles")
    metadata=directory/"nord-est-z15.json"
    with TestClient(create_app(cfg)) as client:
        url="/api/maps/regions/nord-est-z15.pmtiles"
        assert client.delete(url).status_code==403
        bad=client.delete(url,headers={"X-Pharos-Request":"1",
                      "Origin":"https://not-pharos.example"})
        assert bad.status_code==403
        assert target.exists() and metadata.exists()
        response=client.delete(url,headers={"X-Pharos-Request":"1"})
        assert response.status_code==200
        assert response.json()["deleted"]=="nord-est-z15.pmtiles"
        assert response.json()["metadata_removed"] is True
        assert not target.exists() and not metadata.exists()
        assert other.exists()
        remaining=client.get("/api/maps/regions").json()["items"]
        assert [r["id"] for r in remaining]==["italy.pmtiles"]
        assert client.delete(url,headers={"X-Pharos-Request":"1"}).status_code==404


def test_delete_rejects_active_download_and_missing_nas(prepared):
    cfg=load_config(prepared)
    directory=cfg.storage.data_dir/"maps"/"regions"
    directory.mkdir(parents=True)
    target=_archive(directory,"italy.pmtiles")
    app=create_app(cfg)
    with TestClient(app) as client:
        now=int(time.time())
        job_id=uuid.uuid4().hex
        with app.state.jobs._lock, app.state.jobs._db() as db:
            db.execute("""INSERT INTO jobs
                (id,kind,status,label,params,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (job_id,"download-map","queued","italy","{}",now,now))
        url="/api/maps/regions/italy.pmtiles"
        assert client.delete(url,headers={"X-Pharos-Request":"1"}).status_code==409
        assert target.exists()
        with app.state.jobs._lock, app.state.jobs._db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(job_id,))
        (cfg.storage.data_dir/".pharos-volume").unlink()
        assert client.delete(url,headers={"X-Pharos-Request":"1"}).status_code==503
        assert target.exists()


def test_delete_rejects_other_files_and_symlinks(prepared):
    cfg=load_config(prepared)
    directory=cfg.storage.data_dir/"maps"/"regions"
    directory.mkdir(parents=True)
    _archive(directory,"italy.pmtiles")
    with TestClient(create_app(cfg)) as client:
        headers={"X-Pharos-Request":"1"}
        assert client.delete("/api/maps/regions/italy.json",headers=headers).status_code==422
        assert client.delete("/api/maps/regions/invalid-name",headers=headers).status_code==422
        assert (directory/"italy.pmtiles").exists()
