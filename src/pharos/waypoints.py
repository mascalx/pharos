"""Persistent, map-independent waypoint records on the local SQLite filesystem.

Visual basemap archives remain read-only and are never used for waypoint storage.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
import sqlite3
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

_lock = threading.RLock()


class WaypointInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    latitude: float
    longitude: float


def _validated(data: WaypointInput) -> tuple[str, str, float, float]:
    name = data.name.strip()
    if not name:
        raise HTTPException(422, detail="waypoint_name_required")
    if (not math.isfinite(data.latitude) or not -90 <= data.latitude <= 90 or
            not math.isfinite(data.longitude) or not -180 <= data.longitude <= 180):
        raise HTTPException(422, detail="waypoint_coordinates_invalid")
    return name, data.description.strip(), data.latitude, data.longitude


def _guard(request: Request) -> None:
    if request.headers.get("X-Pharos-Request") != "1":
        raise HTTPException(403, detail="request_header_required")
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != request.headers.get("host", "").lower():
            raise HTTPException(403, detail="origin_mismatch")


def router(config) -> APIRouter:
    api = APIRouter()
    database: Path = config.storage.database_dir / "pharos-waypoints.sqlite3"

    @contextmanager
    def connect():
        database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS waypoints (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
            latitude REAL NOT NULL, longitude REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
        try:
            yield db
        finally:
            db.close()

    def as_record(row):
        return {key: row[key] for key in
                ("id", "name", "description", "latitude", "longitude", "created_at", "updated_at")}

    @api.get("/api/maps/waypoints")
    def list_waypoints():
        with _lock, connect() as db:
            rows = db.execute("SELECT * FROM waypoints ORDER BY name COLLATE NOCASE, id").fetchall()
            return {"items": [as_record(row) for row in rows]}

    @api.post("/api/maps/waypoints", status_code=201)
    def add_waypoint(payload: WaypointInput, request: Request):
        _guard(request)
        values = _validated(payload)
        identifier = str(uuid.uuid4())
        with _lock, connect() as db:
            db.execute("INSERT INTO waypoints(id,name,description,latitude,longitude) VALUES(?,?,?,?,?)",
                       (identifier, *values))
            row = db.execute("SELECT * FROM waypoints WHERE id=?", (identifier,)).fetchone()
            db.commit()
        return as_record(row)

    @api.put("/api/maps/waypoints/{identifier}")
    def edit_waypoint(identifier: uuid.UUID, payload: WaypointInput, request: Request):
        _guard(request)
        values = _validated(payload)
        with _lock, connect() as db:
            result = db.execute("""UPDATE waypoints SET name=?,description=?,latitude=?,longitude=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                (*values, str(identifier)))
            if result.rowcount != 1:
                raise HTTPException(404, detail="waypoint_not_found")
            row = db.execute("SELECT * FROM waypoints WHERE id=?", (str(identifier),)).fetchone()
            db.commit()
        return as_record(row)

    @api.delete("/api/maps/waypoints/{identifier}")
    def remove_waypoint(identifier: uuid.UUID, request: Request):
        _guard(request)
        with _lock, connect() as db:
            result = db.execute("DELETE FROM waypoints WHERE id=?", (str(identifier),))
            if result.rowcount != 1:
                raise HTTPException(404, detail="waypoint_not_found")
            db.commit()
        return {"deleted": str(identifier)}

    return api
