"""dev18: category-based POI search and schema-1 backward compatibility.

No real PBF, AI endpoint, Internet, or changes to published indexes.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from pharos.maps_search import list_indexes, search, search_category


def fixture(tmp_path, schema="2"):
    root = tmp_path / "db" / "maps-search"
    root.mkdir(parents=True)
    config = SimpleNamespace(storage=SimpleNamespace(database_dir=tmp_path/"db",
                                                      data_dir=tmp_path/"data"))
    file = root / "sample.sqlite3"
    conn=sqlite3.connect(file)
    try:
        kind = ", poi_kind TEXT NOT NULL DEFAULT ''" if schema == "2" else ""
        conn.executescript(f"""
            CREATE TABLE entries (id INTEGER PRIMARY KEY, osm_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, category TEXT NOT NULL, detail TEXT NOT NULL,
                latitude REAL NOT NULL, longitude REAL NOT NULL,
                searchable TEXT NOT NULL {kind});
            CREATE VIRTUAL TABLE entries_fts USING fts5(
                name,detail,searchable,content='entries',content_rowid='id',
                tokenize='unicode61 remove_diacritics 2');
            CREATE VIRTUAL TABLE entries_rtree USING rtree(
                id,min_lat,max_lat,min_lon,max_lon);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        items = [
            ("n1","Bologna","place",44.494,11.343,""),
            ("n2","Modena","place",44.647,10.925,""),
            ("n3","Pinacoteca Civica","poi",44.496,11.346,"tourism:museum"),
            ("n4","Museo della Musica","poi",44.491,11.348,"tourism:museum"),
            ("n5","Biblioteca Comunale","poi",44.493,11.342,"amenity:library"),
            ("n6","Museo del Parco","poi",44.647,10.927,"tourism:museum"),
            ("n7","Caffè del Museo","poi",44.491,11.344,"amenity:cafe"),
            ("n8","Trattoria delle Torri","poi",44.494,11.346,"amenity:restaurant"),
            ("n9","Ristorante di Modena","poi",44.647,10.927,"amenity:restaurant"),
            ("n10","Farmacia Comunale","poi",44.495,11.341,"amenity:pharmacy"),
            ("n11","Farmacia Universitaria","poi",44.493,11.345,"healthcare:pharmacy"),
            ("n12","Erboristeria del Centro","poi",44.494,11.343,"shop:chemist"),
        ]
        for i,(osm_id,name,kind_value,lat,lon,poi_kind) in enumerate(items,1):
            row=(i,osm_id,name,kind_value,"",lat,lon,"")
            if schema=="2":
                row+= (poi_kind,)
            conn.execute(
                "INSERT INTO entries VALUES (" + ",".join("?" for _ in row)+")",row)
            conn.execute("INSERT INTO entries_fts(rowid,name,detail,searchable) VALUES(?,?,?,?)",(i,name,"",""))
            conn.execute("INSERT INTO entries_rtree VALUES(?,?,?,?,?)",(i,lat,lat,lon,lon))
        conn.executemany("INSERT INTO metadata VALUES(?,?)",[
            ("schema",schema),("area","sample"),("count",str(len(items))),
            ("created_at","1795000000"),("source","OpenStreetMap / Geofabrik")])
        conn.commit()
    finally:
        conn.close()
    return config,file


def test_v2_category_museums_near_locality_without_name_keyword(tmp_path):
    config,file=fixture(tmp_path)
    before=file.read_bytes()
    meta=list_indexes(config)["items"][0]
    assert meta["category_ready"] is True
    assert meta["schema"]=="2"
    result=search_category(config,"museum","Bologna",limit=20)
    assert result["indexed_areas"]==1
    assert not result["requires_reindex"]
    assert {x["name"] for x in result["items"]} == {
        "Pinacoteca Civica", "Museo della Musica"}
    assert all(x["locality_hint"]=="Bologna" for x in result["items"])
    assert all(x["distance_km"] < 8 for x in result["items"])
    assert file.read_bytes()==before, "Search must never mutate the index"
    # Named-POI lookup continues to use its pre-existing FTS5/RTree engine.
    assert any(x["name"]=="Pinacoteca Civica"
               for x in search(config,"Pinacoteca Civica")["items"])


def test_existing_v2_indexes_support_restaurants_and_pharmacies(tmp_path):
    config,file=fixture(tmp_path)
    before=file.read_bytes()
    restaurants=search_category(config,"restaurant","Bologna",limit=20)
    pharmacies=search_category(config,"pharmacy","Bologna",limit=20)
    assert [x["name"] for x in restaurants["items"]] == ["Trattoria delle Torri"]
    assert {x["name"] for x in pharmacies["items"]} == {
        "Farmacia Comunale", "Farmacia Universitaria"}
    assert all(x["category"]=="poi" for x in restaurants["items"]+pharmacies["items"])
    assert all(x["locality_hint"]=="Bologna" for x in pharmacies["items"])
    assert not restaurants["requires_reindex"]
    assert not pharmacies["requires_reindex"]
    assert file.read_bytes()==before, "Adding categories must not require a new index"


def test_v1_remains_readable_but_category_requires_upgrade(tmp_path):
    config,file=fixture(tmp_path,schema="1")
    before=file.read_bytes()
    assert list_indexes(config)["items"][0]["category_ready"] is False
    result=search_category(config,"museum","Bologna")
    assert result["items"]==[]
    assert result["requires_reindex"]==["sample"]
    assert any(x["name"]=="Museo della Musica"
               for x in search(config,"Museo della Musica")["items"])
    assert file.read_bytes()==before


def test_category_rejects_unsupported_categories_or_missing_town(tmp_path):
    config,_=fixture(tmp_path)
    for category in ("all","bar","museum' OR 1=1--"):
        try:
            search_category(config,category,"Bologna")
            raise AssertionError("unsupported category accepted")
        except ValueError:
            pass
    try:
        search_category(config,"museum","")
        raise AssertionError("missing town accepted")
    except ValueError:
        pass


def test_many_restaurants_return_the_nearest_not_an_arbitrary_first_500(tmp_path):
    config,file=fixture(tmp_path)
    with sqlite3.connect(file) as db:
        for i in range(20,540):
            db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?)",
                       (i, f"n{i}", f"Ristorante Lontano {i}",
                        "poi","",44.54,11.343,"","amenity:restaurant"))
            db.execute("INSERT INTO entries_rtree VALUES (?,?,?,?,?)",
                       (i,44.54,44.54,11.343,11.343))
        db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?)",
                   (540,"n540","Ristorante Centrale","poi","",
                    44.494,11.343,"","amenity:restaurant"))
        db.execute("INSERT INTO entries_rtree VALUES (?,?,?,?,?)",
                   (540,44.494,44.494,11.343,11.343))
        db.execute("UPDATE metadata SET value=(SELECT count(*) FROM entries) WHERE key='count'")
        db.commit()
    result=search_category(config,"restaurant","Bologna",limit=5)
    assert result["items"]
    assert result["items"][0]["name"]=="Ristorante Centrale"
    assert len(result["items"])==5
