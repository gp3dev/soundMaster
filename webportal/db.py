import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Measurement:
    ts: float
    level_db: float
    weighting: str = "A"
    response: str = "SLOW"
    range_min: int = 30
    range_max: int = 130
    overflow: bool = False
    underflow: bool = False


@dataclass
class Settings:
    valid_from: float
    location_name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    zone: Optional[str] = None
    limit_day: Optional[float] = None
    limit_night: Optional[float] = None
    comment: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[float] = None
    station_id: Optional[int] = None


@dataclass
class Station:
    id: int
    name: str
    slug: str
    api_key_hash: str
    created_at: float
    revoked_at: Optional[float] = None
    last_seen_at: Optional[float] = None


_local = threading.local()
_db_path: Optional[Path] = None


class _LeqAggregate:
    """SQLite user-defined aggregate: LAeq = 10·log10(⌀ 10^(L/10))."""
    def __init__(self) -> None:
        self._sum = 0.0
        self._n = 0

    def step(self, db: float) -> None:
        if db is not None:
            self._sum += 10 ** (db / 10)
            self._n += 1

    def finalize(self) -> Optional[float]:
        return 10 * math.log10(self._sum / self._n) if self._n else None


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        assert _db_path is not None, "db.init() must be called first"
        _local.conn = sqlite3.connect(str(_db_path))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
        _local.conn.create_aggregate("leq", 1, _LeqAggregate)
    return _local.conn


def init(path: Path) -> None:
    global _db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            slug          TEXT NOT NULL UNIQUE,
            api_key_hash  TEXT NOT NULL,
            created_at    REAL NOT NULL,
            revoked_at    REAL,
            last_seen_at  REAL
        );

        CREATE TABLE IF NOT EXISTS measurements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id  INTEGER NOT NULL REFERENCES stations(id),
            ts          REAL NOT NULL,
            level_db    REAL NOT NULL,
            weighting   TEXT,
            response    TEXT,
            range_min   INTEGER,
            range_max   INTEGER,
            overflow    INTEGER,
            underflow   INTEGER,
            UNIQUE(station_id, ts)
        );
        CREATE INDEX IF NOT EXISTS measurements_station_ts ON measurements(station_id, ts);

        CREATE TABLE IF NOT EXISTS settings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id    INTEGER NOT NULL REFERENCES stations(id),
            created_at    REAL NOT NULL,
            valid_from    REAL NOT NULL,
            location_name TEXT,
            lat           REAL,
            lon           REAL,
            zone          TEXT,
            limit_day     REAL,
            limit_night   REAL,
            comment       TEXT,
            UNIQUE(station_id, valid_from)
        );
        CREATE INDEX IF NOT EXISTS settings_station_valid_from ON settings(station_id, valid_from);

        CREATE TABLE IF NOT EXISTS auth (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    c.commit()


# ── Measurements ─────────────────────────────────────────────────────────────

def insert_many(measurements: list[Measurement], station_id: int) -> int:
    """Insert measurements for a station, ignoring duplicates (same station_id+ts).
    Returns the number of rows actually inserted."""
    c = _conn()
    inserted = 0
    for m in measurements:
        cur = c.execute(
            "INSERT OR IGNORE INTO measurements "
            "(station_id, ts, level_db, weighting, response, range_min, range_max, overflow, underflow) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (station_id, m.ts, m.level_db, m.weighting, m.response,
             m.range_min, m.range_max, int(m.overflow), int(m.underflow)),
        )
        inserted += cur.rowcount
    c.commit()
    return inserted


def latest(station_id: int) -> Optional[Measurement]:
    c = _conn()
    row = c.execute(
        "SELECT * FROM measurements WHERE station_id = ? ORDER BY ts DESC LIMIT 1",
        (station_id,),
    ).fetchone()
    return _row_to_measurement(row) if row else None


def query(station_id: int, since: float, until: float, limit: int = 1000) -> list[Measurement]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM measurements WHERE station_id = ? AND ts >= ? AND ts <= ? "
        "ORDER BY ts DESC LIMIT ?",
        (station_id, since, until, limit),
    ).fetchall()
    return [_row_to_measurement(r) for r in rows]


def stats(station_id: int, since: float) -> dict:
    c = _conn()
    row = c.execute(
        "SELECT MIN(level_db) AS min_db, MAX(level_db) AS max_db, "
        "AVG(level_db) AS avg_db, COUNT(*) AS count "
        "FROM measurements WHERE station_id = ? AND ts >= ?",
        (station_id, since),
    ).fetchone()
    if row and row["count"]:
        return {
            "min_db": round(row["min_db"], 1),
            "max_db": round(row["max_db"], 1),
            "avg_db": round(row["avg_db"], 1),
            "count": row["count"],
        }
    return {"min_db": None, "max_db": None, "avg_db": None, "count": 0}


def query_aggregate(station_id: int, since: float, until: float, bucket_seconds: float) -> list[dict]:
    """Return time-bucketed LAeq/min/max for the given window and station.

    Each bucket covers `bucket_seconds` seconds. Returns rows ordered by time with:
    ts_unix, leq_db, min_db, max_db, count.
    """
    c = _conn()
    rows = c.execute(
        """
        SELECT
          CAST(ts / ? AS INTEGER) * ? AS bucket_ts,
          leq(level_db)            AS leq_db,
          MIN(level_db)            AS min_db,
          MAX(level_db)            AS max_db,
          COUNT(*)                 AS n
        FROM measurements
        WHERE station_id = ? AND ts BETWEEN ? AND ?
        GROUP BY CAST(ts / ? AS INTEGER)
        ORDER BY bucket_ts
        """,
        (bucket_seconds, bucket_seconds, station_id, since, until, bucket_seconds),
    ).fetchall()
    return [
        {
            "ts_unix": row["bucket_ts"],
            "leq_db": round(row["leq_db"], 1) if row["leq_db"] is not None else None,
            "min_db": round(row["min_db"], 1),
            "max_db": round(row["max_db"], 1),
            "count":  row["n"],
        }
        for row in rows
    ]


def query_daily_levels(station_id: int, since: float, until: float) -> list[dict]:
    """Return per-calendar-day L_Tag, L_Nacht, and L_DEN for the given range/station.

    Time periods (local time):
    - L_Tag  (16. BImSchV): 06:00–22:00
    - L_Nacht (16. BImSchV): 22:00–06:00
    - L_DEN components (EU 2002/49/EG): day 07–19, evening 19–23 (+5 dB), night 23–07 (+10 dB)
    """
    c = _conn()
    rows = c.execute(
        """
        SELECT
          date(ts, 'unixepoch', 'localtime') AS day,
          leq(CASE WHEN CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER)
                   BETWEEN 6 AND 21 THEN level_db END)      AS l_tag,
          leq(CASE WHEN CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER)
                   NOT BETWEEN 6 AND 21 THEN level_db END)  AS l_nacht,
          leq(CASE WHEN CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER)
                   BETWEEN 7 AND 18 THEN level_db END)      AS l_day_den,
          leq(CASE WHEN CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER)
                   BETWEEN 19 AND 22 THEN level_db END)     AS l_eve_den,
          leq(CASE WHEN CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER)
                   NOT BETWEEN 7 AND 22 THEN level_db END)  AS l_night_den,
          leq(level_db)                                     AS l_total,
          COUNT(*)                                          AS n
        FROM measurements
        WHERE station_id = ? AND ts BETWEEN ? AND ?
        GROUP BY day
        ORDER BY day
        """,
        (station_id, since, until),
    ).fetchall()

    result = []
    for row in rows:
        l_d, l_e, l_n = row["l_day_den"], row["l_eve_den"], row["l_night_den"]
        l_den = None
        if l_d is not None and l_e is not None and l_n is not None:
            l_den = round(10 * math.log10(
                12 / 24 * 10 ** (l_d / 10) +
                 4 / 24 * 10 ** ((l_e + 5) / 10) +
                 8 / 24 * 10 ** ((l_n + 10) / 10)
            ), 1)
        result.append({
            "day":     row["day"],
            "l_tag":   round(row["l_tag"],   1) if row["l_tag"]   is not None else None,
            "l_nacht": round(row["l_nacht"], 1) if row["l_nacht"] is not None else None,
            "l_den":   l_den,
            "l_total": round(row["l_total"], 1) if row["l_total"] is not None else None,
            "count":   row["n"],
        })
    return result


def query_hourly_profile(station_id: int, since: float, until: float) -> list[dict]:
    """Return energy-averaged LAeq per hour-of-day across all days in range, for a station."""
    c = _conn()
    rows = c.execute(
        """
        SELECT
          CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER) AS hour,
          leq(level_db)  AS leq_db,
          MIN(level_db)  AS min_db,
          MAX(level_db)  AS max_db,
          COUNT(*)       AS n
        FROM measurements
        WHERE station_id = ? AND ts BETWEEN ? AND ?
        GROUP BY hour
        ORDER BY hour
        """,
        (station_id, since, until),
    ).fetchall()
    return [
        {
            "hour":   row["hour"],
            "leq_db": round(row["leq_db"], 1) if row["leq_db"] is not None else None,
            "min_db": round(row["min_db"], 1),
            "max_db": round(row["max_db"], 1),
            "count":  row["n"],
        }
        for row in rows
    ]


def query_raw_export(station_id: int, since: float, until: float):
    """Return a cursor over (ts, level_db, weighting, response) in chronological order."""
    return _conn().execute(
        "SELECT ts, level_db, weighting, response FROM measurements "
        "WHERE station_id = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        (station_id, since, until),
    )


# ── Auth (global admin) ──────────────────────────────────────────────────────

def auth_get(key: str) -> Optional[str]:
    row = _conn().execute("SELECT value FROM auth WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def auth_set(key: str, value: str) -> None:
    c = _conn()
    c.execute("INSERT OR REPLACE INTO auth (key, value) VALUES (?, ?)", (key, value))
    c.commit()


# ── Settings (per station, time-versioned) ───────────────────────────────────

def settings_get(station_id: int, ts: Optional[float] = None) -> Optional[Settings]:
    """Return the settings valid at time ts (default: now) for a station."""
    if ts is None:
        ts = time.time()
    row = _conn().execute(
        "SELECT * FROM settings WHERE station_id = ? AND valid_from <= ? "
        "ORDER BY valid_from DESC LIMIT 1",
        (station_id, ts),
    ).fetchone()
    return _row_to_settings(row) if row else None


def settings_list(station_id: int) -> list[Settings]:
    """Return all settings records for a station ordered by valid_from ascending."""
    rows = _conn().execute(
        "SELECT * FROM settings WHERE station_id = ? ORDER BY valid_from ASC",
        (station_id,),
    ).fetchall()
    return [_row_to_settings(r) for r in rows]


def settings_get_by_id(id: int) -> Optional[Settings]:
    """Return a single settings record by its id, regardless of station or validity."""
    row = _conn().execute("SELECT * FROM settings WHERE id = ?", (id,)).fetchone()
    return _row_to_settings(row) if row else None


def settings_save(station_id: int, s: Settings) -> int:
    """Insert a new settings record for a station, return its id."""
    now = time.time()
    c = _conn()
    cur = c.execute(
        """
        INSERT OR IGNORE INTO settings
          (station_id, created_at, valid_from, location_name, lat, lon, zone, limit_day, limit_night, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (station_id, now, s.valid_from, s.location_name, s.lat, s.lon,
         s.zone, s.limit_day, s.limit_night, s.comment),
    )
    c.commit()
    return cur.lastrowid


def settings_update(id: int, s: Settings) -> bool:
    """Update an existing settings record in place (e.g. retroactive corrections).

    Unlike settings_save(), this modifies the row itself rather than adding a new
    period, so it doesn't shift the record's position in the valid_from history.
    Raises sqlite3.IntegrityError if the new valid_from collides with another
    record of the same station.
    """
    c = _conn()
    cur = c.execute(
        """
        UPDATE settings
        SET valid_from = ?, location_name = ?, lat = ?, lon = ?, zone = ?,
            limit_day = ?, limit_night = ?, comment = ?
        WHERE id = ?
        """,
        (s.valid_from, s.location_name, s.lat, s.lon, s.zone,
         s.limit_day, s.limit_night, s.comment, id),
    )
    c.commit()
    return cur.rowcount > 0


def settings_delete(id: int) -> bool:
    """Delete a settings record by id. Returns True if a row was deleted."""
    c = _conn()
    cur = c.execute("DELETE FROM settings WHERE id = ?", (id,))
    c.commit()
    return cur.rowcount > 0


# ── Stations ──────────────────────────────────────────────────────────────────

def station_create(name: str, slug: str, api_key_hash: str) -> Station:
    c = _conn()
    now = time.time()
    cur = c.execute(
        "INSERT INTO stations (name, slug, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
        (name, slug, api_key_hash, now),
    )
    c.commit()
    return Station(id=cur.lastrowid, name=name, slug=slug, api_key_hash=api_key_hash,
                   created_at=now, revoked_at=None, last_seen_at=None)


def station_list() -> list[Station]:
    """All stations, including revoked ones (admin view)."""
    rows = _conn().execute("SELECT * FROM stations ORDER BY created_at ASC").fetchall()
    return [_row_to_station(r) for r in rows]


def station_list_active() -> list[Station]:
    rows = _conn().execute(
        "SELECT * FROM stations WHERE revoked_at IS NULL ORDER BY created_at ASC"
    ).fetchall()
    return [_row_to_station(r) for r in rows]


def station_get_by_id(id: int) -> Optional[Station]:
    row = _conn().execute("SELECT * FROM stations WHERE id = ?", (id,)).fetchone()
    return _row_to_station(row) if row else None


def station_get_by_slug(slug: str) -> Optional[Station]:
    row = _conn().execute("SELECT * FROM stations WHERE slug = ?", (slug,)).fetchone()
    return _row_to_station(row) if row else None


def station_revoke(id: int) -> bool:
    c = _conn()
    cur = c.execute("UPDATE stations SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                     (time.time(), id))
    c.commit()
    return cur.rowcount > 0


def station_set_key_hash(id: int, new_hash: str) -> bool:
    """Set a new API-key hash for a station and clear any revocation (regenerate)."""
    c = _conn()
    cur = c.execute(
        "UPDATE stations SET api_key_hash = ?, revoked_at = NULL WHERE id = ?",
        (new_hash, id),
    )
    c.commit()
    return cur.rowcount > 0


def station_touch_last_seen(id: int, ts: float) -> None:
    c = _conn()
    c.execute("UPDATE stations SET last_seen_at = ? WHERE id = ?", (ts, id))
    c.commit()


# ── Row mapping ───────────────────────────────────────────────────────────────

def _row_to_settings(row: sqlite3.Row) -> Settings:
    return Settings(
        id=row["id"],
        created_at=row["created_at"],
        valid_from=row["valid_from"],
        location_name=row["location_name"],
        lat=row["lat"],
        lon=row["lon"],
        zone=row["zone"],
        limit_day=row["limit_day"],
        limit_night=row["limit_night"],
        comment=row["comment"],
        station_id=row["station_id"],
    )


def _row_to_measurement(row: sqlite3.Row) -> Measurement:
    return Measurement(
        ts=row["ts"],
        level_db=row["level_db"],
        weighting=row["weighting"] or "A",
        response=row["response"] or "SLOW",
        range_min=row["range_min"] or 30,
        range_max=row["range_max"] or 130,
        overflow=bool(row["overflow"]),
        underflow=bool(row["underflow"]),
    )


def _row_to_station(row: sqlite3.Row) -> Station:
    return Station(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        api_key_hash=row["api_key_hash"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        last_seen_at=row["last_seen_at"],
    )
