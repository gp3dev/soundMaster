import csv
import io
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth as _auth
import db as database

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = FastAPI(title="SoundMaster Webportal", version="1.0")

_COOKIE = "sm_session"


def _require_auth(sm_session: Optional[str] = Cookie(None)) -> None:
    if not _auth.validate_session(sm_session):
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time_range(
    since: Optional[str],
    until: Optional[str],
    days: Optional[int],
    default_days: int = 30,
) -> tuple[float, float]:
    now = time.time()
    if days is not None:
        return now - days * 86400, now
    if since is None and until is None:
        return now - default_days * 86400, now
    try:
        ts_since = datetime.fromisoformat(since).timestamp() if since else now - default_days * 86400
        ts_until = datetime.fromisoformat(until).timestamp() if until else now
        return ts_since, ts_until
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {e}")


def _resolve_station(slug: str) -> database.Station:
    st = database.station_get_by_slug(slug)
    if st is None:
        raise HTTPException(status_code=404, detail=f"Unknown station: {slug}")
    return st


def _measurement_to_dict(m) -> dict:
    return {
        "ts": _ts_to_iso(m.ts),
        "ts_unix": m.ts,
        "level_db": m.level_db,
        "weighting": m.weighting,
        "response": m.response,
        "range_min": m.range_min,
        "range_max": m.range_max,
        "overflow": m.overflow,
        "underflow": m.underflow,
    }


@app.get("/login", include_in_schema=False)
def web_login_page():
    return FileResponse(os.path.join(_WEB_DIR, "login.html"))


@app.get("/settings", include_in_schema=False)
def web_settings():
    return FileResponse(os.path.join(_WEB_DIR, "settings.html"))


@app.get("/profile", include_in_schema=False)
def web_profile():
    return FileResponse(os.path.join(_WEB_DIR, "profile.html"))


@app.get("/calendar", include_in_schema=False)
def web_calendar():
    return FileResponse(os.path.join(_WEB_DIR, "calendar.html"))


@app.get("/daily", include_in_schema=False)
def web_daily():
    return FileResponse(os.path.join(_WEB_DIR, "daily.html"))


@app.get("/export", include_in_schema=False)
def web_export_page():
    return FileResponse(os.path.join(_WEB_DIR, "export.html"))


@app.get("/report", include_in_schema=False)
def web_report():
    return FileResponse(os.path.join(_WEB_DIR, "report-print.html"))


@app.get("/map", include_in_schema=False)
def web_map():
    return FileResponse(os.path.join(_WEB_DIR, "map.html"))


@app.get("/history")
def history(
    station: str = Query(..., description="Station slug"),
    since: Optional[str] = Query(None, description="ISO 8601 start time"),
    until: Optional[str] = Query(None, description="ISO 8601 end time"),
    minutes: Optional[int] = Query(None, description="Last N minutes (alternative to since/until)"),
    limit: int = Query(1000, ge=1, le=10000),
):
    """Raw measurements for a station in a time range."""
    st = _resolve_station(station)
    now = time.time()

    if minutes is not None:
        ts_since = now - minutes * 60
        ts_until = now
    else:
        try:
            ts_since = datetime.fromisoformat(since).timestamp() if since else now - 3600
            ts_until = datetime.fromisoformat(until).timestamp() if until else now
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid timestamp: {e}")

    measurements = database.query(st.id, ts_since, ts_until, limit)
    return {
        "count": len(measurements),
        "since": _ts_to_iso(ts_since),
        "until": _ts_to_iso(ts_until),
        "data": [_measurement_to_dict(m) for m in measurements],
    }


@app.get("/stats")
def stats(
    station: str = Query(..., description="Station slug"),
    minutes: int = Query(60, ge=1, le=10080, description="Statistics window in minutes"),
):
    """Min/max/avg level for a station over a rolling window."""
    st = _resolve_station(station)
    since = time.time() - minutes * 60
    s = database.stats(st.id, since)
    return {
        "window_minutes": minutes,
        "since": _ts_to_iso(since),
        **s,
    }


@app.get("/history/aggregate")
def history_aggregate(
    station: str = Query(..., description="Station slug"),
    since: Optional[str] = Query(None, description="ISO 8601 start time"),
    until: Optional[str] = Query(None, description="ISO 8601 end time"),
    minutes: Optional[int] = Query(None, description="Last N minutes (alternative to since/until)"),
    buckets: int = Query(200, ge=10, le=2000, description="Number of time buckets"),
):
    """Time-bucketed LAeq/min/max for a station — used for chart rendering."""
    st = _resolve_station(station)
    now = time.time()

    if minutes is not None:
        ts_since = now - minutes * 60
        ts_until = now
    else:
        try:
            ts_since = datetime.fromisoformat(since).timestamp() if since else now - 3600
            ts_until = datetime.fromisoformat(until).timestamp() if until else now
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid timestamp: {e}")

    bucket_seconds = max(1.0, (ts_until - ts_since) / buckets)
    rows = database.query_aggregate(st.id, ts_since, ts_until, bucket_seconds)
    return {
        "count": len(rows),
        "since": _ts_to_iso(ts_since),
        "until": _ts_to_iso(ts_until),
        "bucket_seconds": round(bucket_seconds, 1),
        "data": [{"ts": _ts_to_iso(r["ts_unix"]), **r} for r in rows],
    }


@app.get("/stats/daily")
def stats_daily(
    station: str = Query(..., description="Station slug"),
    since: Optional[str] = Query(None, description="ISO 8601 start"),
    until: Optional[str] = Query(None, description="ISO 8601 end"),
    days:  Optional[int] = Query(None, ge=1, le=365, description="Last N days"),
):
    """Per-day L_Tag, L_Nacht and L_DEN (16. BImSchV / EU 2002/49/EG)."""
    st = _resolve_station(station)
    ts_since, ts_until = _parse_time_range(since, until, days, default_days=30)
    rows = database.query_daily_levels(st.id, ts_since, ts_until)
    return {"count": len(rows), "since": _ts_to_iso(ts_since), "until": _ts_to_iso(ts_until), "data": rows}


@app.get("/stats/hourly-profile")
def stats_hourly_profile(
    station: str = Query(..., description="Station slug"),
    since: Optional[str] = Query(None, description="ISO 8601 start"),
    until: Optional[str] = Query(None, description="ISO 8601 end"),
    days:  Optional[int] = Query(None, ge=1, le=365, description="Last N days"),
):
    """Energy-averaged LAeq per hour-of-day across all days in range."""
    st = _resolve_station(station)
    if since is None and until is None and days is None:
        days = 7
    ts_since, ts_until = _parse_time_range(since, until, days)
    rows = database.query_hourly_profile(st.id, ts_since, ts_until)
    return {"count": len(rows), "since": _ts_to_iso(ts_since), "until": _ts_to_iso(ts_until), "data": rows}


@app.get("/export/csv")
def export_csv(
    station: str = Query(..., description="Station slug"),
    since: Optional[str] = Query(None, description="ISO 8601 start"),
    until: Optional[str] = Query(None, description="ISO 8601 end"),
    days:  Optional[int] = Query(None, ge=1, le=3650, description="Last N days"),
):
    """Download all raw measurements in the given range as CSV."""
    st = _resolve_station(station)
    ts_since, ts_until = _parse_time_range(since, until, days, default_days=7)
    cursor = database.query_raw_export(st.id, ts_since, ts_until)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp_iso", "timestamp_unix", "level_db_A", "weighting", "response"])
    for row in cursor:
        w.writerow([
            _ts_to_iso(row[0]),
            f"{row[0]:.3f}",
            f"{row[1]:.1f}",
            row[2] or "A",
            row[3] or "SLOW",
        ])

    from_str = datetime.fromtimestamp(ts_since).strftime("%Y%m%d")
    to_str   = datetime.fromtimestamp(ts_until).strftime("%Y%m%d")
    filename = f"laerm_{st.slug}_{from_str}_{to_str}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class _LoginIn(BaseModel):
    password: str


@app.get("/auth/status")
def auth_status(sm_session: Optional[str] = Cookie(None)):
    """Whether the current session cookie is a valid, logged-in admin session."""
    return {
        "authenticated": _auth.validate_session(sm_session),
        "configured":    _auth.is_configured(),
    }


@app.post("/auth/login")
def auth_login(body: _LoginIn, response: Response):
    """Log in as the single global admin. Sets the httponly session cookie
    `sm_session` on success."""
    if not _auth.is_configured():
        raise HTTPException(status_code=503, detail="Kein Admin-Passwort konfiguriert")
    if not _auth.verify_password(body.password):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    token = _auth.create_session()
    response.set_cookie(
        _COOKIE, token,
        httponly=True, samesite="strict", max_age=8 * 3600,
    )
    return {"ok": True}


@app.post("/auth/logout")
def auth_logout(response: Response, sm_session: Optional[str] = Cookie(None)):
    """Invalidate the current admin session."""
    if sm_session:
        _auth.destroy_session(sm_session)
    response.delete_cookie(_COOKIE)
    return {"ok": True}


class SettingsIn(BaseModel):
    valid_from: Optional[str] = None     # ISO 8601; default = now
    location_name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    zone: Optional[str] = None
    limit_day: Optional[float] = None
    limit_night: Optional[float] = None
    comment: Optional[str] = None


def _settings_to_dict(s) -> dict:
    return {
        "id":            s.id,
        "created_at":    _ts_to_iso(s.created_at) if s.created_at else None,
        "valid_from":    _ts_to_iso(s.valid_from),
        "valid_from_unix": s.valid_from,
        "location_name": s.location_name,
        "lat":           s.lat,
        "lon":           s.lon,
        "zone":          s.zone,
        "limit_day":     s.limit_day,
        "limit_night":   s.limit_night,
        "comment":       s.comment,
    }


@app.get("/api/settings")
def api_settings_get(
    station: str = Query(..., description="Station slug"),
    at: Optional[str] = Query(None, description="ISO 8601 timestamp"),
):
    """Return the settings valid at the given time (default: now) for a station."""
    st = _resolve_station(station)
    ts = None
    if at is not None:
        try:
            ts = datetime.fromisoformat(at).timestamp()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid timestamp: {e}")
    s = database.settings_get(st.id, ts)
    if s is None:
        raise HTTPException(status_code=404, detail="No settings found")
    return _settings_to_dict(s)


@app.get("/api/settings/history")
def api_settings_history(station: str = Query(..., description="Station slug")):
    """Return all settings records for a station ordered by valid_from."""
    st = _resolve_station(station)
    rows = database.settings_list(st.id)
    return {"count": len(rows), "data": [_settings_to_dict(s) for s in rows]}


@app.post("/api/settings", status_code=201, dependencies=[Depends(_require_auth)])
def api_settings_post(body: SettingsIn, station: str = Query(..., description="Station slug")):
    """Create a new settings period for a station."""
    st = _resolve_station(station)
    now = time.time()
    if body.valid_from is not None:
        try:
            valid_from = datetime.fromisoformat(body.valid_from).timestamp()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid valid_from: {e}")
    else:
        valid_from = now

    s = database.Settings(
        valid_from=valid_from,
        location_name=body.location_name,
        lat=body.lat,
        lon=body.lon,
        zone=body.zone,
        limit_day=body.limit_day,
        limit_night=body.limit_night,
        comment=body.comment,
    )
    database.settings_save(st.id, s)
    saved = database.settings_get(st.id, valid_from)
    return _settings_to_dict(saved)


@app.put("/api/settings/{id}", dependencies=[Depends(_require_auth)])
def api_settings_put(id: int, body: SettingsIn):
    """Update an existing settings period in place (e.g. to correct limits retroactively).

    Fields left out of the request body keep their current value; unlike POST this
    never creates a new period, so the record's position in the history is preserved
    unless valid_from is explicitly changed.
    """
    existing = database.settings_get_by_id(id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Settings record not found")

    valid_from = existing.valid_from
    if body.valid_from is not None:
        try:
            valid_from = datetime.fromisoformat(body.valid_from).timestamp()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid valid_from: {e}")

    updated = database.Settings(
        valid_from=valid_from,
        location_name=body.location_name if body.location_name is not None else existing.location_name,
        lat=body.lat if body.lat is not None else existing.lat,
        lon=body.lon if body.lon is not None else existing.lon,
        zone=body.zone if body.zone is not None else existing.zone,
        limit_day=body.limit_day if body.limit_day is not None else existing.limit_day,
        limit_night=body.limit_night if body.limit_night is not None else existing.limit_night,
        comment=body.comment if body.comment is not None else existing.comment,
    )
    try:
        database.settings_update(id, updated)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Für diesen Zeitpunkt existiert bereits ein anderer Eintrag")
    return _settings_to_dict(database.settings_get_by_id(id))


@app.delete("/api/settings/{id}", status_code=204, dependencies=[Depends(_require_auth)])
def api_settings_delete(id: int):
    """Delete a settings record by id."""
    if not database.settings_delete(id):
        raise HTTPException(status_code=404, detail="Settings record not found")


def mount_static() -> None:
    """Mount the static frontend at "/". Must be called only after every other route
    module (ingest, stations_admin) has already registered its routes — Starlette
    matches routes in registration order, and a Mount("/", ...) matches every path,
    so mounting it first would shadow any route added afterwards."""
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
