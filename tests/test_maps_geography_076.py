"""Offline geographical hierarchy, source discovery and selection tests."""
from __future__ import annotations

from pharos import maps_areas, maps_catalog
from pharos.maps_download import MapJobs
from pharos.app import create_app
from pharos.config import load_config
from fastapi.testclient import TestClient


def _fixture():
    def feature(code, name, parent, box):
        west,south,east,north=box
        return {"type":"Feature","properties":{"id":code,"name":name,"parent":parent},
                "geometry":{"type":"Polygon","coordinates":[[
                    [west,south],[east,south],[east,north],[west,north],[west,south]]]}}
    return {"type":"FeatureCollection","features":[
        feature("europe","Europe",None,[-11,35,35,71]),
        feature("italy","Italy","europe",[6,36,19,48]),
        feature("emilia-romagna","Emilia-Romagna","italy",[9,43,13,46]),
        feature("asia","Asia",None,[20,-10,180,80]),
    ]}


def test_hierarchy_and_geojson_bounds():
    result=maps_areas.parse_catalog(_fixture())
    byid={item["id"]:item for item in result}
    assert byid["italy"]["parent"]=="europe"
    assert byid["emilia-romagna"]["parent"]=="italy"
    assert byid["emilia-romagna"]["bbox"]==[9,43,13,46]
    assert byid["asia"]["parent"] is None


def test_no_fake_regions_and_geojson_fails_closed(monkeypatch):
    from pharos.maps_areas import AreaCatalogError
    monkeypatch.setattr(maps_areas,"_fetch",lambda: maps_areas.parse_catalog(_fixture()))
    maps_areas._cache["items"]=None
    result=maps_areas.areas(refresh=True)
    assert result["status"]=="available"
    assert maps_areas.get_area("emilia-romagna")["name"]=="Emilia-Romagna"
    try:
        maps_areas.get_area("ravenna")
    except AreaCatalogError:
        pass
    else:
        raise AssertionError("Do not invent a region absent from the publisher")
    maps_areas._cache["items"]=None


def test_browse_api_contains_hierarchy(prepared, monkeypatch):
    monkeypatch.setattr("pharos.app.geo_areas", lambda *, cache_path: {
        "items":maps_areas.parse_catalog(_fixture()),"status":"available"})
    with TestClient(create_app(load_config(prepared))) as client:
        data=client.get("/api/maps/areas").json()
        assert data["status"]=="available"
        assert any(a["parent"]=="italy" for a in data["items"])


def test_source_builds_use_date_candidates_when_listing_unreadable(monkeypatch):
    monkeypatch.setattr(maps_catalog,"_listing",lambda: [])
    monkeypatch.setattr(maps_catalog,"_probe",lambda name: name==maps_catalog._candidate_names()[0])
    data=maps_catalog.builds(refresh=True)
    assert data["status"]=="available"
    assert len(data["items"])==1
    maps_catalog._cache["data"]=None
