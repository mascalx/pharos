"""Read-only, deterministic POI category search with local FTS5/RTree indexes.

Categories use original OpenStreetMap tags captured since index schema 2.
Schema-1 databases remain available for named-POI and address searches; we
never present a name-only approximation as an exhaustive category search.
"""
from __future__ import annotations

import math
import sqlite3

from .maps_geosearch import _bbox, _distance_km, _places_named, _words

# Public stable search taxonomy. Expand deliberately with tests, not by
# allowing unrestricted SQL/tag values supplied by model output.
POI_CATEGORIES = {
    "museum": ("tourism:museum", "amenity:museum"),
    "restaurant": ("amenity:restaurant",),
    "pharmacy": ("amenity:pharmacy", "healthcare:pharmacy"),
}


def find_category(config, category: str, locality: str, limit: int,
                  *, indexes, index_path):
    if category not in POI_CATEGORIES:
        raise ValueError("maps_search_category_unsupported")
    if not isinstance(locality, str) or not 2 <= len(locality.strip()) <= 100:
        raise ValueError("maps_search_locality_invalid")
    words = _words(locality.strip())
    if not 1 <= len(words) <= 8:
        raise ValueError("maps_search_locality_invalid")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("maps_search_limit_invalid")

    found = {}
    upgraded = []
    checked = 0
    for meta in indexes:
        if not meta.get("category_ready"):
            upgraded.append(meta["area"])
            continue
        path = index_path(meta["area"])
        try:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                # Locality is grounded in an existing place node, never
                # coordinates inferred by the LLM or an external geocoder.
                places = _places_named(db, words)
                if not places:
                    continue
                checked += 1
                for place in places:
                    latitude, longitude = place["latitude"], place["longitude"]
                    radius = 8.0  # explicit proximity, not municipal boundary
                    south, north, west, east = _bbox(latitude, longitude, radius)
                    kinds = POI_CATEGORIES[category]
                    sql = ("""SELECT e.* FROM entries_rtree r
                          JOIN entries e ON e.id=r.id
                          WHERE e.category='poi' AND e.poi_kind IN ("""
                           + ",".join("?" for _ in kinds) +
                           """) AND r.min_lat BETWEEN ? AND ?
                               AND r.min_lon BETWEEN ? AND ?
                          ORDER BY ((e.latitude-?)*(e.latitude-?) +
                                    (e.longitude-?)*(e.longitude-?) * ?)
                          LIMIT 500""")
                    args = (*kinds, south, north, west, east,
                            latitude, latitude, longitude, longitude,
                            math.cos(math.radians(latitude)) ** 2)
                    for row in db.execute(sql, args):
                        record = dict(row)
                        d = _distance_km(latitude, longitude,
                                         record["latitude"], record["longitude"])
                        if not math.isfinite(d) or d > radius:
                            continue
                        record["locality_hint"] = place["name"]
                        record["locality_distance_km"] = round(d, 2)
                        record["distance_km"] = round(d, 2)
                        record["area"] = meta["area"]
                        key = (meta["area"], record["osm_id"])
                        if key not in found or d < found[key]["distance_km"]:
                            found[key] = record
            finally:
                db.close()
        except (sqlite3.Error, OSError):
            # One damaged derived index must not make other areas unreadable.
            continue
    # Location proximity is a heuristic; never report all matching objects
    # as being administratively inside the requested municipality.
    ordered = sorted(found.values(), key=lambda x: (
        x["distance_km"], x["name"].casefold()))
    for item in ordered:
        item.pop("id", None)
        item.pop("searchable", None)
        item.pop("poi_kind", None)
        item.pop("tags_json", None)
    return {
        "items": ordered[:limit],
        "indexed_areas": checked,
        "requires_reindex": sorted(set(upgraded)),
        "category": category,
        "locality": locality.strip(),
    }
