"""Contract tests for durable map-independent waypoint CRUD API."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pharos.waypoints import router


def client(tmp_path):
    app = FastAPI()
    app.include_router(router(SimpleNamespace(
        storage=SimpleNamespace(database_dir=tmp_path))))
    return TestClient(app)


def test_waypoints_crud_persistence(tmp_path):
    with client(tmp_path) as api:
        assert api.get("/api/maps/waypoints").json() == {"items": []}
        data = {"name": "  Ravenna  ", "description": "Fotografia",
                "latitude": 44.4184, "longitude": 12.2035}
        created = api.post("/api/maps/waypoints", json=data,
                           headers={"X-Pharos-Request": "1"})
        assert created.status_code == 201
        item = created.json()
        assert item["name"] == "Ravenna"
        assert item["latitude"] == 44.4184
        identifier = item["id"]
        edited = api.put("/api/maps/waypoints/" + identifier, json={
            **data, "name": "Marina di Ravenna", "longitude": 12.285},
            headers={"X-Pharos-Request": "1"})
        assert edited.status_code == 200
        assert edited.json()["longitude"] == 12.285
    # Same local database with a fresh router, not coupled to the PMTiles.
    with client(tmp_path) as api:
        listed = api.get("/api/maps/waypoints").json()["items"]
        assert len(listed) == 1 and listed[0]["name"] == "Marina di Ravenna"
        missing = api.delete("/api/maps/waypoints/00000000-0000-4000-8000-000000000001",
                             headers={"X-Pharos-Request": "1"})
        assert missing.status_code == 404
        deleted = api.delete("/api/maps/waypoints/" + identifier,
                             headers={"X-Pharos-Request": "1"})
        assert deleted.status_code == 200
        assert api.get("/api/maps/waypoints").json()["items"] == []


def test_waypoints_reject_invalid_and_cross_origin(tmp_path):
    with client(tmp_path) as api:
        endpoint = "/api/maps/waypoints"
        data = {"name": "Test", "description": "", "latitude": 44,
                "longitude": 12}
        assert api.post(endpoint, json=data).status_code == 403
        assert api.post(endpoint, json=data, headers={
            "X-Pharos-Request": "1", "Origin": "https://other.example"}).status_code == 403
        for invalid in [
            {**data, "latitude": 91},
            {**data, "longitude": -181},
            {**data, "name": "   "},
            {**data, "name": "x" * 121},
        ]:
            assert api.post(endpoint, json=invalid,
                            headers={"X-Pharos-Request": "1"}).status_code == 422
        assert api.get(endpoint).json() == {"items": []}
        assert api.delete(endpoint + "/not-a-uuid",
                          headers={"X-Pharos-Request": "1"}).status_code == 422
