"""Offline persistence regression for the Geofabrik area hierarchy."""
from pharos import maps_areas


def test_geographic_hierarchy_survives_process_restart(tmp_path, monkeypatch):
    cache = tmp_path / "maps-geography.json"
    entries = [
        {"id": "europe", "parent": None, "name": "Europe", "bbox": [-11, 34, 33, 72],
         "zoom": 9, "downloadable": True},
        {"id": "italy", "parent": "europe", "name": "Italy", "bbox": [6, 35, 19, 48],
         "zoom": 11, "downloadable": True},
        {"id": "nord-est", "parent": "italy", "name": "Nord-Est",
         "bbox": [10, 43, 14, 46], "zoom": 12, "downloadable": True},
    ]
    monkeypatch.setattr(maps_areas, "_cache", {"expires": 0., "items": None})
    monkeypatch.setattr(maps_areas, "_fetch", lambda: entries)
    assert maps_areas.areas(cache_path=cache)["status"] == "available"
    assert cache.is_file()
    # Mimic a new process with no in-memory data and no network.
    monkeypatch.setattr(maps_areas, "_cache", {"expires": 0., "items": None})

    def unavailable():
        raise OSError("offline")

    monkeypatch.setattr(maps_areas, "_fetch", unavailable)
    result = maps_areas.areas(cache_path=cache)
    assert result["status"] == "cached"
    assert [item["id"] for item in result["items"]] == ["europe", "italy", "nord-est"]


def test_invalid_geographic_cache_fails_closed(tmp_path, monkeypatch):
    cache = tmp_path / "maps-geography.json"
    cache.write_text('{"schema":1,"items":[{"id":"../escape"}]}', encoding="utf-8")
    monkeypatch.setattr(maps_areas, "_cache", {"expires": 0., "items": None})

    def unavailable():
        raise OSError("offline")

    monkeypatch.setattr(maps_areas, "_fetch", unavailable)
    result = maps_areas.areas(cache_path=cache)
    assert result == {"items": [], "status": "unavailable"}
