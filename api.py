import csv
import io
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import db as database

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = FastAPI(title="SoundMaster API", version="1.0")

# Shared connection state set by main
_connected: bool = False
_last_error: str = ""


def set_connection_state(connected: bool, error: str = "") -> None:
    global _connected, _last_error
    _connected = connected
    _last_error = error


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


@app.get("/live", include_in_schema=False)
def web_live():
    return FileResponse(os.path.join(_WEB_DIR, "live.html"))


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


@app.get("/health")
def health():
    return {
        "status": "ok" if _connected else "disconnected",
        "connected": _connected,
        "error": _last_error or None,
    }


@app.get("/current")
def current():
    m = database.latest()
    if m is None:
        raise HTTPException(status_code=503, detail="No measurements available yet")
    return _measurement_to_dict(m)


@app.get("/history")
def history(
    since: Optional[str] = Query(None, description="ISO 8601 start time"),
    until: Optional[str] = Query(None, description="ISO 8601 end time"),
    minutes: Optional[int] = Query(None, description="Last N minutes (alternative to since/until)"),
    limit: int = Query(1000, ge=1, le=10000),
):
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

    measurements = database.query(ts_since, ts_until, limit)
    return {
        "count": len(measurements),
        "since": _ts_to_iso(ts_since),
        "until": _ts_to_iso(ts_until),
        "data": [_measurement_to_dict(m) for m in measurements],
    }


@app.get("/stats")
def stats(minutes: int = Query(60, ge=1, le=10080, description="Statistics window in minutes")):
    since = time.time() - minutes * 60
    s = database.stats(since)
    return {
        "window_minutes": minutes,
        "since": _ts_to_iso(since),
        **s,
    }


@app.get("/history/aggregate")
def history_aggregate(
    since: Optional[str] = Query(None, description="ISO 8601 start time"),
    until: Optional[str] = Query(None, description="ISO 8601 end time"),
    minutes: Optional[int] = Query(None, description="Last N minutes (alternative to since/until)"),
    buckets: int = Query(200, ge=10, le=2000, description="Number of time buckets"),
):
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
    rows = database.query_aggregate(ts_since, ts_until, bucket_seconds)
    return {
        "count": len(rows),
        "since": _ts_to_iso(ts_since),
        "until": _ts_to_iso(ts_until),
        "bucket_seconds": round(bucket_seconds, 1),
        "data": [{"ts": _ts_to_iso(r["ts_unix"]), **r} for r in rows],
    }


@app.get("/stats/daily")
def stats_daily(
    since: Optional[str] = Query(None, description="ISO 8601 start"),
    until: Optional[str] = Query(None, description="ISO 8601 end"),
    days:  Optional[int] = Query(None, ge=1, le=365, description="Last N days"),
):
    """Per-day L_Tag, L_Nacht and L_DEN (16. BImSchV / EU 2002/49/EG)."""
    ts_since, ts_until = _parse_time_range(since, until, days, default_days=30)
    rows = database.query_daily_levels(ts_since, ts_until)
    return {"count": len(rows), "since": _ts_to_iso(ts_since), "until": _ts_to_iso(ts_until), "data": rows}


@app.get("/stats/hourly-profile")
def stats_hourly_profile(
    since: Optional[str] = Query(None, description="ISO 8601 start"),
    until: Optional[str] = Query(None, description="ISO 8601 end"),
    days:  Optional[int] = Query(None, ge=1, le=365, description="Last N days"),
):
    """Energy-averaged LAeq per hour-of-day across all days in range."""
    if since is None and until is None and days is None:
        days = 7
    ts_since, ts_until = _parse_time_range(since, until, days)
    rows = database.query_hourly_profile(ts_since, ts_until)
    return {"count": len(rows), "since": _ts_to_iso(ts_since), "until": _ts_to_iso(ts_until), "data": rows}


@app.get("/export/csv")
def export_csv(
    since: Optional[str] = Query(None, description="ISO 8601 start"),
    until: Optional[str] = Query(None, description="ISO 8601 end"),
    days:  Optional[int] = Query(None, ge=1, le=3650, description="Last N days"),
):
    """Download all raw measurements in the given range as CSV."""
    ts_since, ts_until = _parse_time_range(since, until, days, default_days=7)
    cursor = database.query_raw_export(ts_since, ts_until)

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
    filename = f"laerm_a6_{from_str}_{to_str}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")


class ApiServer:
    def __init__(self, host: str, port: int) -> None:
        self._config = uvicorn.Config(app, host=host, port=port,
                                      log_level="warning", access_log=False)
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="api-server")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
