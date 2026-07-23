import os
import re
import secrets
import time

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import auth as _auth
import db as database
from api import _WEB_DIR, _require_auth, app


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "station"
    slug = base
    n = 2
    while database.station_get_by_slug(slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _station_public_dict(s: database.Station) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "slug": s.slug,
        "last_seen_at": s.last_seen_at,
    }


def _station_admin_dict(s: database.Station) -> dict:
    return {
        **_station_public_dict(s),
        "created_at": s.created_at,
        "revoked_at": s.revoked_at,
    }


@app.get("/api/stations")
def stations_public():
    """Public listing used by the station picker on every page."""
    return [_station_public_dict(s) for s in database.station_list_active()]


@app.get("/api/stations/admin", dependencies=[Depends(_require_auth)])
def stations_admin_list():
    """Full listing incl. revoked stations, for the admin UI."""
    return [_station_admin_dict(s) for s in database.station_list()]


class StationCreateIn(BaseModel):
    name: str


@app.post("/api/stations", status_code=201, dependencies=[Depends(_require_auth)])
def stations_create(body: StationCreateIn):
    """Create a new station and return its API key. The key is shown only in this
    response — it is stored as a hash and cannot be retrieved again afterwards."""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    slug = _slugify(body.name)
    raw_key = secrets.token_urlsafe(32)
    st = database.station_create(body.name.strip(), slug, _auth.hash_password(raw_key))
    return {**_station_admin_dict(st), "api_key": raw_key}


@app.post("/api/stations/{id}/revoke", dependencies=[Depends(_require_auth)])
def stations_revoke(id: int):
    """Revoke a station's API key immediately. Its historical data is kept."""
    if not database.station_revoke(id):
        raise HTTPException(status_code=404, detail="Station nicht gefunden oder bereits widerrufen")
    return {"ok": True}


@app.post("/api/stations/{id}/regenerate-key", dependencies=[Depends(_require_auth)])
def stations_regenerate(id: int):
    """Issue a new API key for a station (also un-revokes it). The old key stops
    working immediately; the new key is shown only in this response."""
    st = database.station_get_by_id(id)
    if st is None:
        raise HTTPException(status_code=404, detail="Station nicht gefunden")
    raw_key = secrets.token_urlsafe(32)
    database.station_set_key_hash(id, _auth.hash_password(raw_key))
    return {"api_key": raw_key}


@app.get("/stations", include_in_schema=False)
def web_stations_page():
    return FileResponse(os.path.join(_WEB_DIR, "stations.html"))


@app.get("/healthz")
def healthz():
    """Docker liveness check — process is up, independent of any station's device status."""
    return {"status": "ok", "time": time.time()}
