"""No-delete coverage checks and authentic PMTiles header zoom handling."""
from __future__ import annotations
import json

from fastapi.testclient import TestClient
from pharos.app import create_app
from pharos.config import load_config
from pharos.maps_coverage import archived_regions, comparison, contains, redundancy


def _archive(folder, name, bbox, zoom, source="20261117.pmtiles"):
    path=folder/(name+".pmtiles")
    data=bytearray(127)
    data[0:8]=b"PMTiles"+bytes([3])
    data[100]=0
    data[101]=zoom
    path.write_bytes(data)
    (folder/(name+".json")).write_text(json.dumps({
        "schema":1,"id":name,"bbox":bbox,"source":source,
        "requested_zoom":zoom}),encoding="utf-8")
    return path


def test_nested_map_coverage_and_zoom(tmp_path):
    italy=[6.5,35.4,18.7,47.2]
    region=[9.0,43.5,13.2,45.2]
    _archive(tmp_path,"italy",italy,12)
    _archive(tmp_path,"emilia-romagna",region,11)
    entries=archived_regions(tmp_path)
    assert len(entries)==2
    local=next(e for e in entries if e["id"]=="emilia-romagna")
    assert redundancy(local,entries)=={
        "potentially_redundant":True,"same_source":True,
        "covered_by":[{"id":"italy","maxzoom":12}]}
    assert comparison({"bbox":region,"zoom":12},entries)["status"]=="covered"
    assert comparison({"bbox":region,"zoom":14},entries)["status"]=="lower_detail"
    assert comparison({"bbox":[-6,22,3,34],"zoom":10},entries)["status"]=="new"
    assert contains(region,italy) is False
    assert comparison({"bbox":italy,"zoom":11},entries)["higher_detail"] == [{"id":"italy","maxzoom":12}]


def test_high_detail_small_region_not_redundant(tmp_path):
    _archive(tmp_path,"italy",[6.5,35.4,18.7,47.2],10)
    _archive(tmp_path,"emilia-romagna",[9,43.5,13.2,45.2],14)
    entries=archived_regions(tmp_path)
    local=next(e for e in entries if e["id"]=="emilia-romagna")
    assert redundancy(local,entries)["potentially_redundant"] is False
    italy=next(e for e in entries if e["id"]=="italy")
    assert redundancy(italy,entries)["potentially_redundant"] is False
    higher=comparison({"bbox":italy["bbox"],"zoom":10},entries)["higher_detail"]
    assert higher==[{"id":"emilia-romagna","maxzoom":14}]


def test_no_metadata_means_no_fabricated_coverage(tmp_path):
    path=tmp_path/"legacy.pmtiles"
    data=bytearray(127)
    data[:8]=b"PMTiles"+bytes([3])
    data[101]=12
    path.write_bytes(data)
    assert archived_regions(tmp_path)==[]
    (tmp_path/"legacy.json").write_text('{"schema":1,"id":"legacy","bbox":[0,0,1,1],"source":"http://invalid"}')
    assert archived_regions(tmp_path)==[]


def test_api_reports_coverage_without_removing_archives(prepared):
    cfg=load_config(prepared)
    folder=cfg.storage.data_dir/"maps"/"regions"
    folder.mkdir(parents=True,exist_ok=True)
    a=_archive(folder,"italy",[6,35,19,48],12)
    b=_archive(folder,"emilia-romagna",[9,43,13,46],10)
    with TestClient(create_app(cfg)) as client:
        response=client.get("/api/maps/regions")
        assert response.status_code==200
        items=response.json()["items"]
        italy=next(x for x in items if x["id"]=="italy.pmtiles")
        local=next(x for x in items if x["id"]=="emilia-romagna.pmtiles")
        assert italy["coverage"]["maxzoom"]==12
        assert local["coverage"]["potentially_redundant"] is True
    assert a.exists() and b.exists()


def test_higher_zoom_job_has_separate_destination(prepared, monkeypatch):
    from pharos.maps_download import MapJobs
    from pharos.download_jobs import DownloadJobs, JobError
    from pharos.kiwix_runtime import KiwixManagedService
    cfg = load_config(prepared)
    monkeypatch.setattr("pharos.maps_download.get_area",
                        lambda area_id: {"id":area_id, "name":"Italy",
                                         "bbox":[6,35,19,48], "zoom":11})
    monkeypatch.setattr("pharos.maps_download.validate_source",
                        lambda source: "https://build.protomaps.com/" + source)
    monkeypatch.setattr(MapJobs, "resume", lambda self: None)
    manager=MapJobs(cfg, DownloadJobs(cfg,KiwixManagedService(cfg)))
    original=manager.submit("italy","20261117.pmtiles")
    upgraded=manager.submit("italy","20261117.pmtiles",14)
    assert original["id"]!=upgraded["id"]
    items={job["label"]:job for job in manager.jobs.list()}
    assert set(items)=={"italy","italy-z14"}
    assert items["italy-z14"]["status"]=="queued"
    try:
        manager.submit("italy","20261117.pmtiles",16)
    except JobError as exc:
        assert str(exc)=="maps_zoom_invalid"
    else:
        raise AssertionError("Out of range zoom should be rejected")
