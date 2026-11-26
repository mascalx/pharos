"""PHAROS 0.7.0.dev20: generic OSM tag search, no fixed category taxonomy.

SQLite-only tests: verify arbitrary tag pairs, AND/OR clauses, multi-value
cuisine, unnamed mapped POIs, v2 backwards compatibility and read-only
behaviour. Does not download map data or call a model.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from pharos.maps_search import (
    GeoSearchError, indexed_tag_keys, index_file, list_indexes, search,
    search_tag_clauses,
)


def config(tmp_path):
    root=tmp_path/"db"/"maps-search"
    root.mkdir(parents=True)
    return SimpleNamespace(storage=SimpleNamespace(
        data_dir=tmp_path/"data",database_dir=tmp_path/"db"))


def make_index(cfg, *, schema="3"):
    path=index_file(cfg,"demo")
    with sqlite3.connect(path) as db:
        third=(",poi_kind TEXT NOT NULL DEFAULT '', "
               "tags_json TEXT NOT NULL DEFAULT '{}'") if schema=="3" else (
               ",poi_kind TEXT NOT NULL DEFAULT ''")
        db.executescript(f"""
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY,osm_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, category TEXT NOT NULL,
                detail TEXT NOT NULL,latitude REAL NOT NULL,
                longitude REAL NOT NULL,searchable TEXT NOT NULL {third});
            CREATE VIRTUAL TABLE entries_fts USING fts5(
                name,detail,searchable,content='entries',content_rowid='id',
                tokenize='unicode61 remove_diacritics 2');
            CREATE VIRTUAL TABLE entries_rtree USING rtree(
                id,min_lat,max_lat,min_lon,max_lon);
            CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        if schema=="3":
            db.executescript("""
                CREATE TABLE entry_tags(entry_id INTEGER NOT NULL,
                    tag_key TEXT NOT NULL,tag_value TEXT NOT NULL);
                CREATE INDEX entry_tags_lookup ON
                    entry_tags(tag_key,tag_value,entry_id);
            """)
        points=[
            ("Bologna","place",44.494,11.343,{}),
            ("Modena","place",44.647,10.925,{}),
            ("Biblioteca del Borgo","poi",44.495,11.344,{"amenity":"library"}),
            ("Forno Sole","poi",44.494,11.342,{"shop":"bakery"}),
            ("Laboratorio Volta","poi",44.493,11.344,{"craft":"electrician"}),
            ("Trattoria Quadrata","poi",44.496,11.345,
              {"amenity":"restaurant","cuisine":"pizza","wheelchair":"yes"}),
            ("Trattoria delle Torri","poi",44.495,11.346,
              {"amenity":"restaurant","cuisine":"italian"}),
            ("Farmacia Comunale","poi",44.493,11.343,{"amenity":"pharmacy"}),
            ("Farmacia Centrale","poi",44.492,11.342,{"healthcare":"pharmacy"}),
            ("amenity=parking","poi",44.493,11.347,{"amenity":"parking"}),
            ("Forno di Modena","poi",44.647,10.928,{"shop":"bakery"}),
        ]
        for i,(name,kind,lat,lon,tags) in enumerate(points,1):
            row=(i,f"n{i}",name,kind,"",lat,lon,"")
            if schema=="3":
                import json
                row+=(next((k+":"+v for k,v in tags.items()),""),
                      json.dumps(tags,separators=(",",":")))
            else:
                row+=(next((k+":"+v for k,v in tags.items()),""),)
            db.execute("INSERT INTO entries VALUES (" +
                       ",".join("?" for _ in row)+")",row)
            db.execute("INSERT INTO entries_fts(rowid,name,detail,searchable) VALUES (?,?,?,?)",(i,name,"",""))
            db.execute("INSERT INTO entries_rtree VALUES (?,?,?,?,?)",
                       (i,lat,lat,lon,lon))
            if schema=="3":
                for key,val in tags.items():
                    db.execute("INSERT INTO entry_tags VALUES (?,?,?)",
                               (i,key,val))
        db.executemany("INSERT INTO metadata VALUES(?,?)",[
            ("schema",schema),("area","demo"),("count",str(len(points))),
            ("created_at","1795000000"),("source","OpenStreetMap / Geofabrik")])
        db.commit()
    return path


@pytest.mark.parametrize(("clauses","expected"),[
    ([{"amenity":"library"}],{"Biblioteca del Borgo"}),
    ([{"shop":"bakery"}],{"Forno Sole"}),
    ([{"craft":"electrician"}],{"Laboratorio Volta"}),
    ([{"amenity":"restaurant","cuisine":"pizza"}],{"Trattoria Quadrata"}),
    ([{"amenity":"pharmacy"},{"healthcare":"pharmacy"}],
     {"Farmacia Comunale","Farmacia Centrale"}),
    ([{"amenity":"parking"}],{"amenity=parking"}),
    ([{"wheelchair":"yes","cuisine":"pizza"}],{"Trattoria Quadrata"}),
])
def test_arbitrary_categories_without_python_catalog(tmp_path,clauses,expected):
    cfg=config(tmp_path)
    path=make_index(cfg)
    before=path.read_bytes()
    data=search_tag_clauses(cfg,clauses,"Bologna",limit=20)
    assert data["indexed_areas"]==1
    assert data["tag_pairs_available"] is True
    assert data["locality_found"] is True
    assert not data["requires_reindex"]
    assert {x["name"] for x in data["items"]}==expected
    assert all(x["locality_hint"]=="Bologna" for x in data["items"])
    assert all(x["distance_km"]<8.0 for x in data["items"])
    assert path.read_bytes()==before


def test_index_keys_are_dynamically_derived_from_sqlite(tmp_path):
    cfg=config(tmp_path)
    make_index(cfg)
    assert {"shop","craft","amenity","healthcare","cuisine","wheelchair"}.issubset(
        set(indexed_tag_keys(cfg)))
    metadata=list_indexes(cfg)["items"][0]
    assert metadata["schema"]=="3"
    assert metadata["category_ready"] is True
    assert metadata["tags_ready"] is True


def test_unsupported_osm_tag_is_not_hallucinated(tmp_path):
    cfg=config(tmp_path)
    make_index(cfg)
    data=search_tag_clauses(cfg,[{"shop":"spaceship"}],"Bologna",limit=20)
    assert data["items"]==[]
    assert data["tag_pairs_available"] is False
    assert data["locality_found"] is True


def test_unknown_locality_returns_no_results_not_approximate_coordinates(tmp_path):
    cfg=config(tmp_path)
    make_index(cfg)
    data=search_tag_clauses(cfg,[{"shop":"bakery"}],"Unknownville",limit=20)
    assert data["items"]==[]
    assert data["locality_found"] is False


def test_v2_name_search_remains_valid_and_tag_search_requires_rebuild(tmp_path):
    cfg=config(tmp_path)
    path=make_index(cfg,schema="2")
    before=path.read_bytes()
    assert list_indexes(cfg)["items"][0]["tags_ready"] is False
    assert any(x["name"]=="Biblioteca del Borgo"
               for x in search(cfg,"Biblioteca del Borgo")["items"])
    assert indexed_tag_keys(cfg)==[]
    result=search_tag_clauses(cfg,[{"amenity":"library"}],"Bologna",limit=20)
    assert result["items"]==[]
    assert result["requires_reindex"]==["demo"]
    assert path.read_bytes()==before


@pytest.mark.parametrize("payload",[
    [{"amenity":"restaurant' OR 1=1--"}],
    [{"amenity":"restaurant; DROP TABLE entries"}],
    [{"amenity":"restaurant","cuisine":"pizza","extra":"yes","foo":"bar"}],
    [{"amenity":"restaurant"}]*8,
    [{"amenity":42}],
    [{"__bad":"value"}],
    [],
])
def test_tag_filter_validation_rejects_unsafe_or_unbounded_payloads(tmp_path,payload):
    cfg=config(tmp_path)
    make_index(cfg)
    with pytest.raises(GeoSearchError,match="maps_search_tags_invalid"):
        search_tag_clauses(cfg,payload,"Bologna",limit=20)
