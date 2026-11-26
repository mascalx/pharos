"""Offline map area selection and durable background job smoke tests (no network)."""
from __future__ import annotations

import time
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.maps_download import MapJobs, REGIONS, _platform_asset, selected_region
from pharos.download_jobs import JobError

HEAD = {"X-Pharos-Request": "1"}


def test_region_catalog_and_rejected_requests(prepared, monkeypatch):
    monkeypatch.setattr('pharos.maps_download.validate_source', lambda source: 'https://build.protomaps.com/'+source)
    monkeypatch.setattr('pharos.maps_download.get_area', lambda area_id: selected_region(area_id))
    def pretend_extract(self, jobid, region, source_id):
        self.jobs._change(jobid, status="completed", detail="", progress=100)
    monkeypatch.setattr(MapJobs, "_run", pretend_extract)
    with TestClient(create_app(load_config(prepared))) as client:
        regions = client.get("/api/maps/available-regions").json()["items"]
        assert len(regions) >= 3
        assert regions == list(REGIONS)
        assert client.post("/api/maps/downloads", json={"region_id": "ravenna", "source_id": "20261116.pmtiles"}).status_code == 403
        assert client.post("/api/maps/downloads", headers={**HEAD, "Origin": "https://invalid.example"},
                           json={"region_id": "ravenna", "source_id": "20261116.pmtiles"}).status_code == 403
        assert client.post("/api/maps/downloads", json={"region_id": "not-a-region", "source_id": "20261116.pmtiles"},
                           headers=HEAD).status_code == 409
        result = client.post("/api/maps/downloads", json={"region_id": "ravenna", "source_id": "20261116.pmtiles"},
                             headers=HEAD)
        assert result.status_code == 202
        jobid = result.json()["id"]
        for _ in range(100):
            matching = [job for job in client.get("/api/jobs").json()["items"] if job["id"] == jobid]
            if matching and matching[0]["status"] == "completed":
                break
            time.sleep(0.01)
        assert matching[0]["kind"] == "download-map"
        assert matching[0]["target_view"] == "maps"
        assert matching[0]["status"] == "completed"
        assert client.post("/api/jobs/" + jobid + "/read", headers=HEAD).status_code == 200


def test_map_tool_manifest_platform_and_region_validation():
    assert selected_region("italy")["zoom"] >= 7
    for region in REGIONS:
        lon0, lat0, lon1, lat1 = region["bbox"]
        assert -180 <= lon0 < lon1 <= 180
        assert -90 <= lat0 < lat1 <= 90
    assert _platform_asset() is not None


def test_map_queue_survives_new_instances(prepared, monkeypatch):
    from pharos.download_jobs import DownloadJobs
    from pharos.kiwix_runtime import KiwixManagedService
    cfg = load_config(prepared)
    jobs = DownloadJobs(cfg, KiwixManagedService(cfg))
    manager = MapJobs(cfg, jobs)
    # Suppress worker startup: confirm the queued row survives a new owner.
    monkeypatch.setattr(MapJobs, "resume", lambda self: None)
    monkeypatch.setattr('pharos.maps_download.validate_source', lambda source: 'https://build.protomaps.com/'+source)
    monkeypatch.setattr('pharos.maps_download.get_area', lambda area_id: selected_region(area_id))
    item = manager.submit("ravenna", "20261116.pmtiles")
    assert item["status"] == "queued"
    later = MapJobs(cfg, DownloadJobs(cfg, KiwixManagedService(cfg)))
    rows = later.jobs.list()
    found = next(job for job in rows if job["id"] == item["id"])
    assert found["status"] == "queued"
    assert found["target_view"] == "maps"
    assert later.cancel(item["id"])["status"] == "cancelled"
