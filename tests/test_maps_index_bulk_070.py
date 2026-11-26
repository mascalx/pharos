"""PHAROS dev15: bounded-memory, bulk-build offline geo-index regression.

A small fake PyOsmium handler exercises the *real* SQLite construction
path with no network, installed osmium wheel or original map data.
"""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace

import pytest

from pharos import maps_search
from pharos.maps_search import GeoSearchError, build_index, index_file, search


class Tags(dict):
    pass


class Location:
    lat = 44.417
    lon = 12.199
    def valid(self):
        return True


class Obj:
    def __init__(self, identifier, tags, way=False):
        self.id = identifier
        self.tags = Tags(tags)
        self.location = Location()
        self.nodes = [SimpleNamespace(location=Location()) for _ in range(2)] if way else []


def fake_osmium(monkeypatch, *, many=250):
    class Handler:
        def __init__(self):
            pass

        def apply_file(self, source, *, locations, idx):
            assert locations is True
            assert isinstance(idx, str)
            # A completely untagged way is ignored before expensive geometry
            # processing; keep one searchable named way for coverage.
            self.way(Obj(10_000_000, {}, way=True))
            self.way(Obj(10_000_001, {"name":"Via Roma","highway":"residential"}, way=True))
            self.node(Obj(999_999, {}))
            self.node(Obj(888_888, {"name":"Ravenna","place":"city"}))
            self.node(Obj(777_777, {"name":"Museo dei Mosaici","tourism":"museum"}))
            self.node(Obj(777_776, {
                "name":"Trattoria della Piazza","amenity":"restaurant",
                "cuisine":"italian;pizza","wheelchair":"yes"}))
            self.node(Obj(777_775, {"amenity":"parking"}))
            for i in range(many):
                self.node(Obj(i + 1, {"addr:street":"Via Cavour",
                                      "addr:housenumber":str(i+1),
                                      "addr:city":"Ravenna"}))
    monkeypatch.setitem(sys.modules, "osmium", SimpleNamespace(SimpleHandler=Handler))


def cfg(tmp_path):
    data = tmp_path / "data"
    db = tmp_path / "db"
    data.mkdir()
    db.mkdir()
    return SimpleNamespace(storage=SimpleNamespace(database_dir=db,data_dir=data))


def test_bulk_import_fts_rtree_staged_progress_and_offline_search(tmp_path, monkeypatch):
    fake_osmium(monkeypatch, many=350)
    monkeypatch.setattr(maps_search, "IMPORT_BATCH", 17)
    monkeypatch.setattr(maps_search, "INDEX_BATCH", 40)
    config = cfg(tmp_path)
    source = tmp_path / "fixture.osm.pbf"
    source.write_bytes(b"fake-only")
    phases = []
    result = build_index(
        config,"nord-est",[11.9,44.1,12.4,44.6],source,
        cancelled=lambda: False,
        progress=lambda count: None,
        phase_progress=lambda stage,done,total: phases.append((stage,done,total)))
    assert result == {"area":"nord-est","count":355}
    assert source.is_file(), "Completed build never removes original PBF"
    assert {name for name, *_ in phases} == {
        "maps_search_importing","maps_search_building_fts",
        "maps_search_building_rtree","maps_search_building_tags",
        "maps_search_verifying"}
    assert next(x for x in phases if x[0]=="maps_search_importing")[2] is None
    assert phases[-1] == ("maps_search_verifying",355,355)
    with sqlite3.connect(index_file(config,"nord-est")) as db:
        assert db.execute("SELECT count(*) FROM entries").fetchone()[0] == 355
        assert db.execute("SELECT count(*) FROM entries_rtree").fetchone()[0] == 355
        assert db.execute(
            "SELECT count(*) FROM entries_fts WHERE entries_fts MATCH 'cavour'"
        ).fetchone()[0] == 350
    assert search(config,"Via Cavour 25")["items"]
    assert any(x["name"]=="Via Roma" for x in search(config,"roma")["items"])
    assert any(x["category"]=="poi" for x in search(config,"mosaici")["items"])
    from pharos.maps_search import list_indexes, search_tag_clauses, indexed_tag_keys
    assert list_indexes(config)["items"][0]["tags_ready"] is True
    assert "wheelchair" in indexed_tag_keys(config)
    pizza=search_tag_clauses(
        config,[{"amenity":"restaurant","cuisine":"pizza"}],"Ravenna")
    assert [x["name"] for x in pizza["items"]] == ["Trattoria della Piazza"]
    assert "amenity=restaurant" not in pizza["items"][0]["name"]
    parks=search_tag_clauses(config,[{"amenity":"parking"}],"Ravenna")
    assert any(x["name"]=="amenity=parking" for x in parks["items"])
    with sqlite3.connect(index_file(config,"nord-est")) as db:
        assert db.execute("SELECT tags_json FROM entries WHERE osm_id='n777776'").fetchone()[0]
        assert db.execute("SELECT count(*) FROM entry_tags").fetchone()[0] >= 6
    assert not (config.storage.database_dir / "maps-search" / ".nord-est-").exists()


def test_cancel_during_postprocessing_preserves_existing_index(tmp_path, monkeypatch):
    fake_osmium(monkeypatch, many=80)
    monkeypatch.setattr(maps_search, "IMPORT_BATCH", 10)
    monkeypatch.setattr(maps_search, "INDEX_BATCH", 10)
    config=cfg(tmp_path)
    source=tmp_path/"fixture.osm.pbf"
    source.write_bytes(b"dummy")
    bounds=[11.9,44.1,12.4,44.6]
    build_index(config,"nord-est",bounds,source,
                cancelled=lambda: False,progress=lambda count: None)
    before=index_file(config,"nord-est").read_bytes()
    state={"cancel":False}
    def reporting(stage,done,total):
        if stage=="maps_search_building_fts" and done>=10:
            state["cancel"]=True
    with pytest.raises(GeoSearchError, match="maps_search_cancelled"):
        build_index(config,"nord-est",bounds,source,
                    cancelled=lambda: state["cancel"],
                    progress=lambda count: None,phase_progress=reporting)
    assert index_file(config,"nord-est").read_bytes()==before
    assert source.is_file()
    assert list((config.storage.database_dir/"maps-search").glob(".nord-est-*")) == []


def test_low_local_disk_prevents_import_without_touching_maps(tmp_path, monkeypatch):
    fake_osmium(monkeypatch, many=1)
    config=cfg(tmp_path)
    source=tmp_path/"original.osm.pbf"
    source.write_bytes(b"original file")
    monkeypatch.setattr(maps_search.shutil,"disk_usage",
                        lambda path: SimpleNamespace(total=50 * 1024**3,
                                                      free=12 * 1024**2))
    with pytest.raises(GeoSearchError, match="maps_search_disk_space_low"):
        build_index(config,"nord-est",[11.9,44.1,12.4,44.6],source,
                    cancelled=lambda: False,progress=lambda count: None)
    assert source.read_bytes()==b"original file"
    assert not index_file(config,"nord-est").exists()
