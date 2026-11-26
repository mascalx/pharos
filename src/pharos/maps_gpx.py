"""Offline GPX track storage, geometry and waypoint export.

GPX is inert user data: no DTD/ENTITY, network retrieval, remote URLs or
third-party parsers. Local SQLite is kept separate from PMTiles on NAS.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

MAX_BYTES = 5 * 1024 * 1024
MAX_POINTS = 35000
MAX_SEGMENTS = 500
MAX_TRACKS = 200
_lock = threading.RLock()
_BAD_XML = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.I)
_XML_NAME = re.compile(r"[\x00-\x1f\x7f]")


def _guard(request: Request):
    if request.headers.get("X-Pharos-Request") != "1":
        raise HTTPException(403, detail="request_header_required")
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("http", "https") or
                parsed.netloc.lower() != request.headers.get("host", "").lower()):
            raise HTTPException(403, detail="origin_mismatch")


def _tag(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _children(el, name):
    return [child for child in el if _tag(child) == name]


def _child_text(el, name, limit=200):
    for child in el:
        if _tag(child) == name:
            return (child.text or "").strip()[:limit]
    return ""


def _point(el):
    try:
        lat = float(el.attrib["lat"])
        lon = float(el.attrib["lon"])
    except (KeyError, ValueError, OverflowError):
        raise ValueError("gpx_coordinates_invalid")
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("gpx_coordinates_invalid")
    alt = None
    ele = _child_text(el, "ele", 60)
    if ele:
        try:
            value = float(ele)
            if math.isfinite(value) and -12000 <= value <= 100000:
                alt = value
        except ValueError:
            pass
    return [lon, lat, alt]


def _meters(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = math.radians(((b[0] - a[0] + 180) % 360) - 180)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12742000 * math.asin(min(1., math.sqrt(h)))


def parse_gpx(raw: bytes, filename: str):
    if not raw or len(raw) > MAX_BYTES or _BAD_XML.search(raw):
        raise ValueError("gpx_invalid_xml")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("gpx_invalid_xml") from exc
    if _tag(root) != "gpx":
        raise ValueError("gpx_invalid_xml")
    name = _child_text(_children(root, "metadata")[0], "name") if _children(root, "metadata") else ""
    groups = []
    for track in _children(root, "trk"):
        if not name:
            name = _child_text(track, "name")
        groups.extend(_children(segment, "trkpt") for segment in _children(track, "trkseg"))
    for route in _children(root, "rte"):
        if not name:
            name = _child_text(route, "name")
        groups.append(_children(route, "rtept"))
    segments = []
    bounds = [180., 90., -180., -90.]
    point_count = 0
    distance = gain = loss = 0.
    altitude_pairs = 0
    for group in groups:
        if not group:
            continue
        point_count += len(group)
        if point_count > MAX_POINTS or len(segments) >= MAX_SEGMENTS:
            raise ValueError("gpx_too_complex")
        points = [_point(el) for el in group]
        for lon, lat, _ in points:
            bounds = [min(bounds[0], lon), min(bounds[1], lat),
                      max(bounds[2], lon), max(bounds[3], lat)]
        for a, b in zip(points, points[1:]):
            distance += _meters(a, b)
            if a[2] is not None and b[2] is not None:
                delta = b[2] - a[2]
                # Small vertical GPS jitter is not meaningful altitude gain.
                if abs(delta) >= 3:
                    gain += max(0., delta)
                    loss += max(0., -delta)
                altitude_pairs += 1
        segments.append([[lon, lat] for lon, lat, _ in points])
    if point_count < 2:
        raise ValueError("gpx_track_missing")
    label = name or Path(filename).stem
    label = _XML_NAME.sub("", label).strip()[:120] or "GPX"
    return {"name": label, "segments": segments, "bounds": bounds,
            "point_count": point_count, "distance_m": round(distance, 1),
            "gain_m": round(gain, 1) if altitude_pairs else None,
            "loss_m": round(loss, 1) if altitude_pairs else None}


class TrackRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _clean_track_name(value: str) -> str:
    name = _XML_NAME.sub("", value).strip()
    if not name or len(name) > 120:
        raise HTTPException(422, detail="gpx_name_invalid")
    return name


def router(config) -> APIRouter:
    api = APIRouter()
    database = config.storage.database_dir / "pharos-gpx.sqlite3"

    @contextmanager
    def connect():
        database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS tracks(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, original_file TEXT NOT NULL,
            segments TEXT NOT NULL, bounds TEXT NOT NULL,
            point_count INTEGER NOT NULL, distance_m REAL NOT NULL,
            gain_m REAL, loss_m REAL, created_at TEXT NOT NULL)""")
        try:
            yield db
        finally:
            db.close()

    def summary(row):
        result = {k: row[k] for k in ("id", "name", "original_file", "point_count",
                  "distance_m", "gain_m", "loss_m", "created_at")}
        result["bounds"] = json.loads(row["bounds"])
        return result

    @api.get("/api/maps/gpx")
    def list_tracks():
        with _lock, connect() as db:
            rows = db.execute("""SELECT * FROM tracks ORDER BY created_at DESC,id""").fetchall()
            return {"items": [summary(row) for row in rows]}

    @api.post("/api/maps/gpx", status_code=201)
    async def upload_track(request: Request, file: UploadFile = File(...),
                           name: str | None = Form(None)):
        _guard(request)
        filename = Path(file.filename or "").name
        if not filename.lower().endswith(".gpx") or len(filename) > 200:
            raise HTTPException(422, detail="gpx_filename_invalid")
        try:
            raw = await file.read(MAX_BYTES + 1)
        finally:
            await file.close()
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, detail="gpx_too_large")
        try:
            result = parse_gpx(raw, filename)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if name is not None:
            result["name"] = _clean_track_name(name)
        identifier = str(uuid.uuid4())
        with _lock, connect() as db:
            if db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] >= MAX_TRACKS:
                raise HTTPException(409, detail="gpx_library_full")
            db.execute("""INSERT INTO tracks(id,name,original_file,segments,bounds,
                point_count,distance_m,gain_m,loss_m,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                identifier, result["name"], filename,
                json.dumps(result["segments"], separators=(",", ":")),
                json.dumps(result["bounds"]), result["point_count"],
                result["distance_m"], result["gain_m"], result["loss_m"],
                datetime.now(timezone.utc).isoformat()))
            db.commit()
            row = db.execute("SELECT * FROM tracks WHERE id=?", (identifier,)).fetchone()
        return summary(row)

    @api.get("/api/maps/gpx/{identifier}")
    def get_track(identifier: uuid.UUID):
        with _lock, connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (str(identifier),)).fetchone()
            if row is None:
                raise HTTPException(404, detail="gpx_not_found")
            return {**summary(row), "segments": json.loads(row["segments"])}

    @api.patch("/api/maps/gpx/{identifier}")
    def rename_track(identifier: uuid.UUID, payload: TrackRename, request: Request):
        _guard(request)
        name = _clean_track_name(payload.name)
        with _lock, connect() as db:
            changed = db.execute("UPDATE tracks SET name=? WHERE id=?",
                                 (name, str(identifier)))
            if not changed.rowcount:
                raise HTTPException(404, detail="gpx_not_found")
            db.commit()
            row = db.execute("SELECT * FROM tracks WHERE id=?", (str(identifier),)).fetchone()
        return summary(row)

    @api.delete("/api/maps/gpx/{identifier}")
    def delete_track(identifier: uuid.UUID, request: Request):
        _guard(request)
        with _lock, connect() as db:
            result = db.execute("DELETE FROM tracks WHERE id=?", (str(identifier),))
            if not result.rowcount:
                raise HTTPException(404, detail="gpx_not_found")
            db.commit()
        return {"deleted": str(identifier)}

    @api.get("/api/maps/waypoints/export.gpx")
    def export_waypoints():
        # Query the existing waypoint DB read-only; never alter its schema.
        waypoint_db = config.storage.database_dir / "pharos-waypoints.sqlite3"
        if not waypoint_db.is_file():
            rows = []
        else:
            uri = waypoint_db.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=10) as db:
                rows = db.execute("""SELECT name,description,latitude,longitude
                    FROM waypoints ORDER BY name COLLATE NOCASE,id""").fetchall()
        root = ET.Element("gpx", {"version": "1.1", "creator": "PHAROS",
                                  "xmlns": "http://www.topografix.com/GPX/1/1"})
        for name, description, lat, lon in rows:
            pt = ET.SubElement(root, "wpt", {"lat": str(lat), "lon": str(lon)})
            ET.SubElement(pt, "name").text = name
            if description:
                ET.SubElement(pt, "desc").text = description
        payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return Response(payload, media_type="application/gpx+xml",
                        headers={"Content-Disposition": 'attachment; filename="pharos-waypoints.gpx"',
                                 "X-Content-Type-Options": "nosniff"})

    return api
