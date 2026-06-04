import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
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
