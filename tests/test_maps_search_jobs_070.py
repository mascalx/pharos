"""Small regression tests for the optional search-data jobs and API guards."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from pharos.app import create_app
from pharos.config import load_config
from pharos.download_jobs import DownloadJobs, JobError
from pharos.kiwix_runtime import KiwixManagedService
from pharos.maps_download import MapJobs

OFFICIAL="https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf"


def test_index_only_is_optional_and_persists(prepared, monkeypatch):
    config=load_config(prepared)
    jobs=DownloadJobs(config, KiwixManagedService(config))
    manager=MapJobs(config,jobs)
    monkeypatch.setattr(MapJobs,"resume",lambda self: None)
    monkeypatch.setattr("pharos.maps_areas.get_area",lambda region_id,**kw:{
        "id":region_id,"name":"Nord-Est","bbox":[10,43,14,46],
        "zoom":12,"pbf_url":OFFICIAL})
    with pytest.raises(JobError,match="maps_search_map_missing"):
        manager.submit_index("nord-est")
    from pharos.maps import map_directory
    directory=map_directory(config)
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"nord-est-z15.pmtiles").write_bytes(b"PMTiles" + bytes([3]))
    monkeypatch.setattr("pharos.maps_download.metadata_for_archive",
                        lambda path:{"id":path.stem} if path.is_file() else None)
    item=manager.submit_index("nord-est")
    row=next(j for j in jobs.list() if j["id"]==item["id"])
    assert row["kind"]=="index-map"
    assert row["status"]=="queued"
    assert row["target_view"]=="maps"
    with pytest.raises(JobError,match="maps_search_already_queued"):
        manager.submit_index("nord-est")
    assert manager.cancel(item["id"])["status"]=="cancelled"


def test_maps_search_endpoints_validate_queries_and_mutations(prepared):
    with TestClient(create_app(load_config(prepared))) as client:
        assert client.get("/api/maps/search/indexes").status_code==200
        assert client.get("/api/maps/search?q=Ravenna").status_code==200
        assert client.get("/api/maps/search?q=x").status_code==422
        assert client.post("/api/maps/search/indexes",
                           json={"region_id":"nord-est"}).status_code==403


def test_refresh_existing_index_is_queued_without_deletion(prepared,monkeypatch):
    import json
    from pharos.maps_search import index_file
    from pharos.maps import map_directory
    config=load_config(prepared)
    jobs=DownloadJobs(config,KiwixManagedService(config))
    manager=MapJobs(config,jobs)
    monkeypatch.setattr(MapJobs,"resume",lambda self:None)
    monkeypatch.setattr("pharos.maps_areas.get_area",lambda area_id,**kw:{
        "id":area_id,"name":"Test Region","bbox":[10,43,14,46],
        "zoom":12,"pbf_url":OFFICIAL})
    directory=map_directory(config)
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"nord-est.pmtiles").write_bytes(b"untouched basemap")
    monkeypatch.setattr("pharos.maps_download.metadata_for_archive",
                        lambda path:{"id":path.stem} if path.is_file() else None)
    old_index=index_file(config,"nord-est")
    old_index.parent.mkdir(parents=True,exist_ok=True)
    old_index.write_bytes(b"legacy index must survive queued upgrade")
    with pytest.raises(JobError,match="maps_search_already_ready"):
        manager.submit_index("nord-est")
    request=manager.submit_index("nord-est",refresh=True)
    with jobs._lock,jobs._db() as db:
        row=db.execute("SELECT kind,params FROM jobs WHERE id=?",
                       (request["id"],)).fetchone()
        assert row["kind"]=="index-map"
        assert json.loads(row["params"])["refresh_index"] is True
    assert old_index.read_bytes()==b"legacy index must survive queued upgrade"
    assert manager.cancel(request["id"])["status"]=="cancelled"
    assert old_index.read_bytes()==b"legacy index must survive queued upgrade"
    assert (directory/"nord-est.pmtiles").read_bytes()==b"untouched basemap"


def test_worker_receives_refresh_flag_from_persistent_job(prepared,monkeypatch):
    from threading import Event
    from pharos.maps_search import index_file
    from pharos.maps import map_directory
    config=load_config(prepared)
    jobs=DownloadJobs(config,KiwixManagedService(config))
    manager=MapJobs(config,jobs)
    monkeypatch.setattr("pharos.maps_areas.get_area",lambda area_id,**kw:{
        "id":area_id,"name":"Test Region","bbox":[10,43,14,46],
        "zoom":12,"pbf_url":OFFICIAL})
    folder=map_directory(config)
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"nord-est.pmtiles").write_bytes(b"map")
    monkeypatch.setattr("pharos.maps_download.metadata_for_archive",
                        lambda path:{"id":path.stem} if path.is_file() else None)
    old=index_file(config,"nord-est")
    old.parent.mkdir(parents=True,exist_ok=True)
    old.write_bytes(b"active old index")
    observed=[]
    done=Event()
    def fake_run(self,jobid,region,source_id):
        observed.append(dict(region))
        self.jobs._change(jobid,status="completed",detail="",progress=100)
        done.set()
    monkeypatch.setattr(MapJobs,"_run",fake_run)
    try:
        job=manager.submit_index("nord-est",refresh=True)
        assert done.wait(3), "queued reindex job never reached the worker"
        assert observed and observed[0]["refresh_index"] is True
        assert observed[0]["index_only"] is True
        assert old.read_bytes()==b"active old index"
    finally:
        manager.stop()


def test_v3_index_can_be_rebuilt_explicitly_without_removing_active_file(
        prepared,monkeypatch,tmp_path):
    import json
    import sqlite3
    from pharos.maps_search import index_file
    from pharos.maps import map_directory
    import pharos.maps_download as downloads
    config=load_config(prepared)
    jobs=DownloadJobs(config,KiwixManagedService(config))
    manager=MapJobs(config,jobs)
    monkeypatch.setattr(MapJobs,"resume",lambda self:None)
    monkeypatch.setattr("pharos.maps_areas.get_area",lambda area_id,**kw:{
        "id":area_id,"name":"Test Region","bbox":[10,43,14,46],
        "zoom":12,"pbf_url":OFFICIAL})
    folder=map_directory(config)
    folder.mkdir(parents=True,exist_ok=True)
    pmtiles=folder/"nord-est.pmtiles"
    pmtiles.write_bytes(b"untouched")
    monkeypatch.setattr(downloads,"metadata_for_archive",
                        lambda path:{"id":path.stem} if path.is_file() else None)
    index=index_file(config,"nord-est")
    index.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(index) as db:
        db.execute("CREATE TABLE metadata(key TEXT,value TEXT)")
        db.executemany("INSERT INTO metadata VALUES (?,?)",[
            ("schema","3"),("area","nord-est"),("count","1"),
            ("created_at","1700000000")])
        db.commit()
    previous=index.read_bytes()
    request=manager.submit_index("nord-est",refresh=True)
    with jobs._lock,jobs._db() as db:
        params=json.loads(db.execute(
            "SELECT params FROM jobs WHERE id=?",(request["id"],)).fetchone()[0])
    assert params["refresh_index"] is True
    assert params["base_index_created_at"]==1700000000
    pbf=tmp_path/"fixture.osm.pbf"
    pbf.write_bytes(b"fake source")
    invoked=[]
    monkeypatch.setattr(downloads,"fetch_osm_source",
                        lambda *a,**k:pbf)
    monkeypatch.setattr(downloads,"build_index",
                        lambda *a,**k:invoked.append(a[1]))
    region={"id":"nord-est","search_area_id":"nord-est",
            "bbox":[10,43,14,46],"pbf_url":OFFICIAL,
            "refresh_index":True,"base_index_created_at":1700000000}
    manager._run_index(request["id"],region)
    assert invoked==["nord-est"], "Rebuild must not skip an existing v3"
    assert index.read_bytes()==previous, "No premature overwrite of old index"
    assert pmtiles.read_bytes()==b"untouched"
