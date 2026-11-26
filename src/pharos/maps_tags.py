"""Generic offline tag search over schema-v3 OpenStreetMap-derived SQLite.

The language model suggests bounded tag conjunctions/disjunctions; only
literal pairs present in a local, installed index may be executed. SQL is
parameterized, read-only, with RTree locality and explicit result limits.
No fixed OSM category whitelist or remote geocoder is involved.
"""
from __future__ import annotations

import math
import re
import sqlite3

from .maps_geosearch import _bbox, _distance_km, _places_named, _words

# Conjunctive tag predicates in each clause, disjunctive clauses overall.
# Example pizza: [{"amenity":"restaurant","cuisine":"pizza"}].
# Example pharmacies: [{"amenity":"pharmacy"},{"healthcare":"pharmacy"}].
PAIR_KEY = re.compile(r"[a-z][a-z0-9_:-]{0,79}\Z")
PAIR_VALUE = re.compile(r"[a-z0-9_:+./ -]{1,120}\Z")
MAX_CLAUSES = 4
MAX_TAGS_PER_CLAUSE = 3
MAX_RESULT_CANDIDATES = 400
SEARCH_RADIUS_KM = 8.0


def validate_clauses(clauses):
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= MAX_CLAUSES:
        raise ValueError("maps_search_tags_invalid")
    valid = []
    for clause in clauses:
        if not isinstance(clause, dict) or not 1 <= len(clause) <= MAX_TAGS_PER_CLAUSE:
            raise ValueError("maps_search_tags_invalid")
        item = {}
        for k, v in clause.items():
            if (not isinstance(k, str) or not isinstance(v, str)
                    or not PAIR_KEY.fullmatch(k)
                    or not PAIR_VALUE.fullmatch(v)):
                raise ValueError("maps_search_tags_invalid")
            item[k] = v.casefold()
        valid.append(item)
    return valid


def catalog_keys(config, *, indexes, index_path, limit=180):
    """Actual searchable OSM tag keys in installed v3 indexes."""
    found = set()
    for meta in indexes:
        if not meta.get("tags_ready"):
            continue
        try:
            with sqlite3.connect(index_path(meta["area"]).resolve().as_uri() +
                                 "?mode=ro", uri=True) as db:
                for (key,) in db.execute(
                    "SELECT DISTINCT tag_key FROM entry_tags "
                    "ORDER BY tag_key LIMIT ?", (limit,)):
                    found.add(key)
        except (sqlite3.Error, OSError):
            continue
    return sorted(found)[:limit]


def search_tags(config, clauses, locality, *, limit, indexes, index_path):
    clauses = validate_clauses(clauses)
    if not isinstance(locality, str) or not 2 <= len(locality.strip()) <= 100:
        raise ValueError("maps_search_locality_invalid")
    town = _words(locality.strip())
    if not 1 <= len(town) <= 8:
        raise ValueError("maps_search_locality_invalid")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("maps_search_limit_invalid")
    upgraded = [m["area"] for m in indexes if not m.get("tags_ready")]
    valid_areas = 0
    any_pair_exists = False
    resolved_places = False
    matches = {}
    for meta in indexes:
        if not meta.get("tags_ready"):
            continue
        try:
            db = sqlite3.connect(index_path(meta["area"]).resolve().as_uri() +
                                 "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                places = _places_named(db, town)
                if not places:
                    continue
                resolved_places = True
                valid_areas += 1
                for clause in clauses:
                    pairs = sorted(clause.items())
                    # A proposal is valid only if every key/value can be
                    # proved to exist in this index. This is not proof of
                    # semantic correctness; show interpreted filters to user.
                    if any(db.execute(
                            "SELECT 1 FROM entry_tags "
                            "WHERE tag_key=? AND tag_value=? LIMIT 1",
                            pair).fetchone() is None for pair in pairs):
                        continue
                    any_pair_exists = True
                    first = pairs[0]
                    secondary = pairs[1:]
                    conditions = []
                    params = [first[0], first[1]]
                    for key, val in secondary:
                        conditions.append(
                            "EXISTS (SELECT 1 FROM entry_tags tx "
                            "WHERE tx.entry_id=e.id AND tx.tag_key=? "
                            "AND tx.tag_value=?)")
                        params.extend([key, val])
                    filter_sql = (" AND " + " AND ".join(conditions)) if conditions else ""
                    for place in places:
                        lat, lon = place["latitude"], place["longitude"]
                        south, north, west, east = _bbox(lat, lon, SEARCH_RADIUS_KM)
                        sql = (
                            "SELECT e.id,e.osm_id,e.name,e.category,e.detail,"
                            "e.latitude,e.longitude FROM entry_tags t "
                            "JOIN entries e ON e.id=t.entry_id "
                            "JOIN entries_rtree r ON r.id=e.id "
                            "WHERE t.tag_key=? AND t.tag_value=? "
                            "AND e.category='poi'" + filter_sql +
                            " AND r.min_lat BETWEEN ? AND ?"
                            " AND r.min_lon BETWEEN ? AND ? "
                            "ORDER BY ((e.latitude-?)*(e.latitude-?) + "
                            "(e.longitude-?)*(e.longitude-?) * ?) LIMIT ?")
                        args = (*params, south, north, west, east,
                                lat, lat, lon, lon,
                                math.cos(math.radians(lat)) ** 2,
                                MAX_RESULT_CANDIDATES)
                        for row in db.execute(sql, args):
                            item = dict(row)
                            distance = _distance_km(lat,lon,item["latitude"],item["longitude"])
                            if not math.isfinite(distance) or distance > SEARCH_RADIUS_KM:
                                continue
                            item["area"] = meta["area"]
                            item["distance_km"] = round(distance, 3)
                            item["locality_hint"] = place["name"]
                            item["locality_distance_km"] = round(distance, 3)
                            key = (meta["area"], item["osm_id"])
                            if (key not in matches or
                                    item["distance_km"] < matches[key]["distance_km"]):
                                matches[key] = item
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            continue
    ordered = sorted(matches.values(),key=lambda row: (
        row["distance_km"], row["name"].casefold()))
    for row in ordered:
        row.pop("id", None)
    return {
        "items": ordered[:limit],
        "requires_reindex": sorted(set(upgraded)),
        "tag_pairs_available": any_pair_exists,
        "locality_found": resolved_places,
        "indexed_areas": valid_areas,
        "filters": clauses,
        "locality": locality.strip(),
    }
