import time

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

import auth as _auth
import db as database
from api import app


class MeasurementIn(BaseModel):
    ts: float
    level_db: float
    weighting: str = "A"
    response: str = "SLOW"
    range_min: int = 30
    range_max: int = 130
    overflow: bool = False
    underflow: bool = False


class IngestIn(BaseModel):
    measurements: list[MeasurementIn]


def require_station_key(authorization: str = Header(None)) -> database.Station:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing station API key")
    token = authorization.removeprefix("Bearer ").strip()
    for st in database.station_list_active():
        if _auth.verify_secret(token, st.api_key_hash):
            return st
    raise HTTPException(status_code=401, detail="Invalid or revoked station API key")


@app.post("/api/ingest/measurements")
def ingest_measurements(body: IngestIn, station: database.Station = Depends(require_station_key)):
    """Receive a batch of measurements from an Erfassungsmodul.

    Auth: `Authorization: Bearer <station API key>`. The station is derived solely
    from the key — the request body has no station identifier, so a station can
    never write data under another station's identity. Idempotent: re-sending an
    already-ingested batch (e.g. after a lost response) does not create duplicates.
    """
    ms = [
        database.Measurement(
            ts=m.ts, level_db=m.level_db, weighting=m.weighting, response=m.response,
            range_min=m.range_min, range_max=m.range_max,
            overflow=m.overflow, underflow=m.underflow,
        )
        for m in body.measurements
    ]
    inserted = database.insert_many(ms, station.id)
    database.station_touch_last_seen(station.id, time.time())
    return {"accepted": inserted, "received": len(ms)}
