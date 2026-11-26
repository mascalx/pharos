"""PHAROS 0.7.0.dev16: deterministic locality disambiguation on v1 SQLite.

No PyOsmium, PBF, remote geocoder, map downloads or schema migrations.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from pharos.maps_search import search


def _config(tmp_path):
    data=tmp_path/"data"
    dbroot=tmp_path/"db"
    data.mkdir()
    dbroot.mkdir()
    root=dbroot/"maps-search"
    root.mkdir()
    return SimpleNamespace(storage=SimpleNamespace(database_dir=dbroot,data_dir=data))


def _seed(config, *, exact_local_number=False):
    file=config.storage.database_dir/"maps-search"/"nord-est.sqlite3"
    conn=sqlite3.connect(file)
    try:
        conn.executescript("""
            CREATE TABLE entries(
                id INTEGER PRIMARY KEY, osm_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, category TEXT NOT NULL,
                detail TEXT NOT NULL, latitude REAL NOT NULL,
                longitude REAL NOT NULL, searchable TEXT NOT NULL);
            CREATE VIRTUAL TABLE entries_fts USING fts5(
                name,detail,searchable,content='entries',content_rowid='id',
                tokenize='unicode61 remove_diacritics 2');
            CREATE VIRTUAL TABLE entries_rtree USING rtree(
                id,min_lat,max_lat,min_lon,max_lon);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        entries=[
            ("n100","San Pietro del Fiume","place","",44.446,11.862,""),
            ("n101","Ravenna","place","",44.417,12.199,""),
            ("n102","Bologna","place","",44.495,11.342,""),
            ("w110","Via delle Rose","street","",44.449,11.865,""),
            ("w111","Via delle Rose","street","",44.450,11.866,""),
            ("w112","Via delle Rose","street","",44.448,11.863,""),
            ("w120","Via delle Rose","street","",44.416,12.200,""),
            ("w130","Via delle Rose","street","",44.496,11.346,""),
            ("n140","Via delle Rose 12","address","Ravenna",44.416,12.202,""),
            ("n150","Museo dei Mosaici","poi","",44.420,12.201,""),
        ]
        if exact_local_number:
            entries.append(("n151","Via delle Rose 12","address",
                            "San Pietro del Fiume",44.451,11.867,""))
        for idx,(osm_id,name,category,detail,lat,lon,aliases) in enumerate(entries,1):
            conn.execute(
                "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)",
                (idx,osm_id,name,category,detail,lat,lon,aliases))
            conn.execute(
                "INSERT INTO entries_fts(rowid,name,detail,searchable) VALUES(?,?,?,?)",
                (idx,name,detail,aliases))
            conn.execute("INSERT INTO entries_rtree VALUES(?,?,?,?,?)",
                         (idx,lat,lat,lon,lon))
        conn.executemany("INSERT INTO metadata VALUES(?,?)",[
            ("schema","1"),("area","nord-est"),("count",str(len(entries))),
            ("created_at","1795180000"),("source","OpenStreetMap / Geofabrik")])
        conn.commit()
    finally:
        conn.close()
    return file


def test_locality_suffix_full_address_uses_nearby_road_without_inventing_house(tmp_path):
    config=_config(tmp_path)
    file=_seed(config)
    before=file.read_bytes()
    results=search(config,"Via delle Rose 12 a S.Pietro del Fiume")
    assert results["indexes"]==1
    assert results["items"]
    assert results["items"][0]["category"]=="street"
    assert results["items"][0]["approximate_street"] is True
    assert results["items"][0]["requested_number"]=="12"
    assert results["items"][0]["locality_hint"]=="San Pietro del Fiume"
    assert results["items"][0]["longitude"]<12
    assert len(results["items"])==1, "Nearby way segments must collapse"
    assert file.read_bytes()==before, "Search must not mutate the existing index"


def test_named_roads_disambiguated_without_reindexing(tmp_path):
    config=_config(tmp_path)
    _seed(config)
    results=search(config,"Via delle Rose",limit=30)
    # Three distinct locations, not multiple near-identical way segments.
    roads=[item for item in results["items"] if item["category"]=="street"]
    assert len(roads)==3
    hints={item["locality_hint"] for item in roads}
    assert hints=={"San Pietro del Fiume","Ravenna","Bologna"}
    assert all("approximate_street" not in x for x in roads)
    assert search(config,"Bologna")["items"][0]["category"]=="place"


def test_locality_comma_and_exact_house_number_first(tmp_path):
    config=_config(tmp_path)
    _seed(config,exact_local_number=True)
    result=search(config,"Via delle Rose 12, San Pietro del Fiume")
    assert result["items"][0]["category"]=="address"
    assert not result["items"][0].get("approximate_street")
    assert result["items"][0]["longitude"]<12
    assert result["items"][0]["locality_hint"]=="San Pietro del Fiume"


def test_exact_house_number_no_location_is_not_treated_as_road(tmp_path):
    config=_config(tmp_path)
    _seed(config)
    result=search(config,"Via delle Rose 12")
    assert result["items"]
    assert result["items"][0]["category"]=="address"
    assert result["items"][0]["detail"]=="Ravenna"
    assert not result["items"][0].get("approximate_street")


def test_nearby_existing_poi_search_still_functions(tmp_path):
    config=_config(tmp_path)
    _seed(config)
    result=search(config,"Museo dei Mosaici",latitude=44.42,
                  longitude=12.2,radius_km=4)
    assert any(x["name"]=="Museo dei Mosaici" for x in result["items"])
    assert not search(config,"Museo dei Mosaici",latitude=40.,
                      longitude=12.,radius_km=1)["items"]
