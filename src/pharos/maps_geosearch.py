"""Deterministic, fully offline geographic query resolution on existing OSM indexes.

A town is a geographic *hint*, not a proved administrative boundary.
Road-segment centroids are approximate, and no unrecorded house number is
invented. All queries use parameterized SQLite FTS5 and RTree.
"""
from __future__ import annotations

import math
import re
import unicodedata

TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
CITY_RADIUS_KM = 8.0
NEARBY_RADIUS_KM = 12.0
STREET_TYPES = {"via", "viale", "corso", "piazza", "piazzale", "vicolo",
                "strada", "largo", "contrada", "salita", "discesa"}
STREET_STOP = {"a", "ad", "in", "di", "da", "nel", "nella"}


def _normalized(text: str) -> str:
    # Italian "S." shorthand in town names: S.Anna -> Sant'Anna,
    # S.Pietro -> San Pietro. This is a general linguistic normalization,
    # independent of any particular location or private user example.
    return re.sub(
        r"(?i)\bs\s*\.\s*([a-zà-ÿ]+)\b",
        lambda m: ("Sant'" if m.group(1)[0].lower() in "aeiouàèéìòù" else "San ")
                  + m.group(1),
        text,
    )


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFD", _normalized(text).casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return TOKEN.findall(folded)[:14]


def _match(words: list[str]) -> str:
    return " AND ".join('"' + word + '"*' for word in words)


def _distance_km(a_lat, a_lon, b_lat, b_lon):
    dlat = math.radians(b_lat - a_lat)
    dlon = math.radians(b_lon - a_lon)
    arc = (math.sin(dlat / 2)**2 +
           math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) *
           math.sin(dlon / 2)**2)
    return 12742 * math.asin(min(1.0, math.sqrt(max(0, arc))))


def _bbox(lat, lon, radius_km):
    dlat = radius_km / 111.2
    dlon = radius_km / max(1.0, 111.2 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _places_named(db, words: list[str]):
    if not words:
        return []
    rows = db.execute(
        """SELECT e.name, e.latitude, e.longitude
           FROM entries_fts JOIN entries e ON e.id=entries_fts.rowid
           WHERE entries_fts MATCH ? AND e.category='place'
           ORDER BY bm25(entries_fts) LIMIT 100""",
        (_match(words),)).fetchall()
    # Require all tokens and their order, not just an arbitrary subset.
    return [dict(row) for row in rows if _words(row["name"]) == words][:12]


def _extract_locality(db, text: str):
    normalized = _normalized(text)
    if "," in normalized:
        street, locality = normalized.rsplit(",", 1)
        places = _places_named(db, _words(locality))
        if places and _words(street):
            return _words(street), places
    words = _words(normalized)
    for size in range(min(7, len(words) - 1), 0, -1):
        places = _places_named(db, words[-size:])
        if not places:
            continue
        prefix = words[:-size]
        while prefix and prefix[-1] in STREET_STOP:
            prefix.pop()
        if prefix and (len(prefix) >= 2 or size == 1):
            return prefix, places
    return words, []


def _fetch(db, words, *, categories=None, anchor=None, limit=350, radius_km=None):
    if not words:
        return []
    joins = ""
    where = ["entries_fts MATCH ?"]
    params = [_match(words)]
    if categories:
        where.append("e.category IN (" + ",".join("?" for _ in categories) + ")")
        params.extend(categories)
    if anchor is not None:
        lat, lon = anchor
        radius = radius_km or CITY_RADIUS_KM
        joins = " JOIN entries_rtree r ON r.id=e.id"
        south, north, west, east = _bbox(lat, lon, radius)
        where.extend(("r.min_lat BETWEEN ? AND ?", "r.min_lon BETWEEN ? AND ?"))
        params.extend((south, north, west, east))
    statement = ("""SELECT e.*, bm25(entries_fts) AS relevance
                    FROM entries_fts JOIN entries e ON e.id=entries_fts.rowid"""
                 + joins + " WHERE " + " AND ".join(where)
                 + " ORDER BY relevance LIMIT ?")
    return [dict(row) for row in db.execute(statement, (*params, limit)).fetchall()]


def _nearest_named_place(db, lat, lon):
    south, north, west, east = _bbox(lat, lon, NEARBY_RADIUS_KM)
    rows = db.execute(
        """SELECT e.name,e.latitude,e.longitude FROM entries_rtree r
           JOIN entries e ON e.id=r.id
           WHERE e.category='place'
             AND r.min_lat BETWEEN ? AND ?
             AND r.min_lon BETWEEN ? AND ?
           ORDER BY ((e.latitude-?)*(e.latitude-?) +
                     (e.longitude-?)*(e.longitude-?) * ?) LIMIT 5""",
        (south,north,west,east,lat,lat,lon,lon,
         math.cos(math.radians(lat)) ** 2)).fetchall()
    if not rows:
        return None
    nearest = min(rows, key=lambda row: _distance_km(
        lat, lon, row["latitude"], row["longitude"]))
    distance = _distance_km(lat, lon, nearest["latitude"], nearest["longitude"])
    if distance > NEARBY_RADIUS_KM:
        return None
    return nearest["name"], round(distance, 2)


def _is_number(text):
    return bool(re.fullmatch(r"[0-9]{1,5}[a-z]?", text))


def search_index(db, query: str, *, limit: int, latitude=None, longitude=None,
                 radius_km=None, explicit_subject=False):
    """Return ranked candidates from one already-built area, no reindex needed."""
    words, places = (_words(query), []) if explicit_subject else _extract_locality(db, query)
    if not words:
        return []
    requested_number = words[-1] if len(words) > 2 and _is_number(words[-1]) else None
    named_road = bool(words and words[0] in STREET_TYPES)
    terms = words[:-1] if requested_number else words

    # Collect geographical candidates first. The old FTS-only LIMIT would
    # discard the intended village among hundreds of same-named roads.
    anchors = [(p["latitude"], p["longitude"], p["name"]) for p in places]
    if not anchors:
        anchors = [(None, None, None)]
    all_candidates = {}
    approximate = False

    def retrieve(search_words, categories=None):
        result = {}
        for lat, lon, placename in anchors:
            rows = _fetch(
                db, search_words, categories=categories,
                anchor=(lat,lon) if lat is not None else
                       ((latitude,longitude) if latitude is not None else None),
                radius_km=CITY_RADIUS_KM if lat is not None else radius_km,
                limit=350 if lat is None else 450)
            for row in rows:
                if lat is not None:
                    distance = _distance_km(
                        lat, lon, row["latitude"], row["longitude"])
                    if distance > CITY_RADIUS_KM:
                        continue
                    row["_target_km"] = distance
                    row["_target_name"] = placename
                key = row["osm_id"]
                if key not in result or row.get("_target_km", 1e9) < result[key].get("_target_km", 1e9):
                    result[key] = row
        return result

    if requested_number:
        all_candidates = retrieve(words, ("address",))
        # FTS prefix matching ('21'* also matches '210') is not an exact
        # housenumber match. Verify the actual last number token.
        all_candidates = {
            k:v for k,v in all_candidates.items()
            if _words(v["name"])[-1:] == [requested_number]
        }
        if not all_candidates:
            all_candidates = retrieve(terms, ("street",))
            approximate = True
    elif named_road:
        all_candidates = retrieve(terms, ("street",))
        if not all_candidates:
            all_candidates = retrieve(terms)
    else:
        all_candidates = retrieve(terms)

    hints = {}
    results = []
    for row in all_candidates.values():
        lat, lon = row["latitude"], row["longitude"]
        if latitude is not None:
            distance = _distance_km(latitude, longitude, lat, lon)
            if distance > radius_km:
                continue
            row["distance_km"] = round(distance, 2)
        if row.get("_target_name"):
            # Do not imply an administrative boundary: the place is a
            # distance-based search reference.
            row["locality_hint"] = row["_target_name"]
            row["locality_distance_km"] = round(row["_target_km"], 2)
        elif not row["detail"] and row["category"] in ("street","poi","address"):
            key = (round(lat, 2), round(lon, 2))
            if key not in hints:
                hints[key] = _nearest_named_place(db, lat, lon)
            nearby = hints[key]
            if nearby:
                row["locality_hint"], row["locality_distance_km"] = nearby
        if approximate:
            row["approximate_street"] = True
            row["requested_number"] = requested_number

        phrase = " ".join(terms)
        name = " ".join(_words(row["name"]))
        priority = (100 if name == phrase else
                    65 if name.startswith(phrase) else
                    35 if phrase in name else 0)
        if places:
            priority += max(0, 100 - row.get("_target_km", 100)*13)
        if requested_number and not approximate and row["category"] == "address":
            priority += 85
        if named_road and not requested_number and row["category"] == "street":
            priority += 25
        if approximate:
            priority -= 15
        row["rank"] = priority - row.pop("relevance", 0)
        row.pop("_target_km", None)
        row.pop("_target_name", None)
        results.append(row)
    return sorted(results, key=lambda x: (-x["rank"], x.get("distance_km",float("inf"))))[:max(120, limit*5)]


def collapse_segments(rows, limit):
    """One row per nearby set of identically named road segments."""
    kept = []
    for item in rows:
        if item["category"] == "street":
            repeated = any(
                other["category"] == "street"
                and other["area"] == item["area"]
                and _words(other["name"]) == _words(item["name"])
                and _distance_km(other["latitude"], other["longitude"],
                                 item["latitude"], item["longitude"]) <= 1.5
                for other in kept
            )
            if repeated:
                continue
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept
