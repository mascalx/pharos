"""Offline GPX import/export and persistence regression tests."""
from types import SimpleNamespace
import io
import xml.etree.ElementTree as ET

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pharos.maps_gpx import parse_gpx, router
from pharos.waypoints import router as waypoints_router


SAMPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<gpx version="1.1" creator="Test" xmlns="http://www.topografix.com/GPX/1/1">
 <trk><name>Delta del Po</name>
 <trkseg><trkpt lat="44.1" lon="12.1"><ele>10</ele></trkpt>
 <trkpt lat="44.11" lon="12.11"><ele>28</ele></trkpt>
 </trkseg><trkseg><trkpt lat="44.12" lon="12.12"><ele>50</ele></trkpt>
 <trkpt lat="44.13" lon="12.13"><ele>48</ele></trkpt></trkseg></trk>
</gpx>"""
HEADERS = {"X-Pharos-Request": "1"}


def api(tmp_path):
    storage = SimpleNamespace(database_dir=tmp_path)
    app = FastAPI()
    app.include_router(waypoints_router(SimpleNamespace(storage=storage)))
    app.include_router(router(SimpleNamespace(storage=storage)))
    return TestClient(app)


def test_gpx_stats_do_not_connect_disconnected_segments():
    result = parse_gpx(SAMPLE, "test.gpx")
    assert result["name"] == "Delta del Po"
    assert len(result["segments"]) == 2
    assert result["point_count"] == 4
    assert 2500 < result["distance_m"] < 4000
    assert result["gain_m"] == 18.
    assert result["loss_m"] == 0.
    assert result["bounds"] == [12.1, 44.1, 12.13, 44.13]


@pytest.mark.parametrize("content", [
    b"", b"<foo/>", b"<gpx><wpt lat='44' lon='12'/></gpx>",
    b"<gpx><trk><trkseg><trkpt lat='99' lon='12'/><trkpt lat='44' lon='12'/></trkseg></trk></gpx>",
    b'<!DOCTYPE gpx [<!ENTITY x "hacked">]><gpx>&x;</gpx>',
])
def test_reject_invalid_tracks(content):
    with pytest.raises(ValueError):
        parse_gpx(content, "test.gpx")


def test_gpx_upload_list_detail_delete_and_reopen(tmp_path):
    with api(tmp_path) as client:
        assert client.get("/api/maps/gpx").json()["items"] == []
        assert client.post("/api/maps/gpx", files={"file": ("test.gpx", SAMPLE)},
                           ).status_code == 403
        created = client.post("/api/maps/gpx", files={"file": ("test.gpx", SAMPLE)},
                              headers=HEADERS)
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        assert created.json()["point_count"] == 4
        assert client.get("/api/maps/gpx/"+identifier).json()["segments"][0][0] == [12.1, 44.1]
    with api(tmp_path) as client:
        assert len(client.get("/api/maps/gpx").json()["items"]) == 1
        assert client.delete("/api/maps/gpx/"+identifier, headers=HEADERS).status_code == 200
        assert client.get("/api/maps/gpx").json()["items"] == []
        assert client.delete("/api/maps/gpx/"+identifier, headers=HEADERS).status_code == 404


def test_no_waypoint_data_changed_and_export_gpx(tmp_path):
    with api(tmp_path) as client:
        original = client.post("/api/maps/waypoints", headers=HEADERS,
                               json={"name": "Punto & Belvedere",
                                     "description": "Fiume <Po>", "latitude": 44.5,
                                     "longitude": 12.5})
        assert original.status_code == 201
        assert client.post("/api/maps/gpx", files={"file": ("test.gpx", SAMPLE)},
                           headers=HEADERS).status_code == 201
        exported = client.get("/api/maps/waypoints/export.gpx")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        root = ET.fromstring(exported.content)
        waypoint = next(el for el in root if el.tag.endswith("wpt"))
        assert waypoint.attrib == {"lat": "44.5", "lon": "12.5"}
        assert any(el.text == "Punto & Belvedere" for el in waypoint)
        assert len(client.get("/api/maps/waypoints").json()["items"]) == 1


def test_upload_safety_boundaries(tmp_path):
    with api(tmp_path) as client:
        assert client.post("/api/maps/gpx", headers=HEADERS,
                           files={"file": ("route.txt", SAMPLE)}).status_code == 422
        assert client.post("/api/maps/gpx", headers=HEADERS,
                           files={"file": ("route.gpx", b"z" * (5*1024*1024+1))}).status_code == 413
        assert client.post("/api/maps/gpx",
                           headers={**HEADERS, "Origin": "https://attacker.example"},
                           files={"file": ("route.gpx", SAMPLE)}).status_code == 403
        assert client.get("/api/maps/gpx").json()["items"] == []


def test_import_custom_name_and_rename_existing_track(tmp_path):
    with api(tmp_path) as client:
        response = client.post("/api/maps/gpx",
                               files={"file": ("original.gpx", SAMPLE)},
                               data={"name": "Escursione nel Delta"},
                               headers=HEADERS)
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        assert response.json()["name"] == "Escursione nel Delta"
        renamed = client.patch("/api/maps/gpx/"+identifier,
                               json={"name": "Sentiero del Fiume"},
                               headers=HEADERS)
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Sentiero del Fiume"
        assert renamed.json()["original_file"] == "original.gpx"
        assert client.patch("/api/maps/gpx/"+identifier,
                            json={"name": "  "}, headers=HEADERS).status_code == 422
        assert client.patch("/api/maps/gpx/"+identifier,
                            json={"name": "Una" * 45},
                            headers=HEADERS).status_code == 422
        assert client.patch("/api/maps/gpx/"+identifier,
                            json={"name": "Non autorizzato"}).status_code == 403
        assert client.get("/api/maps/gpx/"+identifier).json()["name"] == "Sentiero del Fiume"
    with api(tmp_path) as client:
        listing = client.get("/api/maps/gpx").json()["items"]
        assert len(listing) == 1
        assert listing[0]["name"] == "Sentiero del Fiume"


def test_invalid_custom_import_name_does_not_create_track(tmp_path):
    with api(tmp_path) as client:
        invalid = client.post("/api/maps/gpx",
                              files={"file": ("original.gpx", SAMPLE)},
                              data={"name": " "},
                              headers=HEADERS)
        assert invalid.status_code == 422
        assert client.get("/api/maps/gpx").json()["items"] == []
