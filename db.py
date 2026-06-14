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
        _local.conn.create_aggregate("leq", 1, _LeqAggregate)
    return _local.conn


def init(path: Path) -> None:
    global _db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS measurements (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        REAL NOT NULL,
            level_db  REAL NOT NULL,
            weighting TEXT,
            response  TEXT,
            range_min INTEGER,
            range_max INTEGER,
            overflow  INTEGER,
            underflow INTEGER
        );
        CREATE INDEX IF NOT EXISTS measurements_ts ON measurements(ts);

        CREATE TABLE IF NOT EXISTS settings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    REAL NOT NULL,
            valid_from    REAL NOT NULL,
            location_name TEXT,
            lat           REAL,
            lon           REAL,
            zone          TEXT,
            limit_day     REAL,
            limit_night   REAL,
            comment       TEXT
        );
        CREATE INDEX IF NOT EXISTS settings_valid_from ON settings(valid_from);

        CREATE TABLE IF NOT EXISTS auth (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    c.commit()


def insert(m: Measurement) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO measurements (ts, level_db, weighting, response, range_min, range_max, overflow, underflow) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (m.ts, m.level_db, m.weighting, m.response, m.range_min, m.range_max,
         int(m.overflow), int(m.underflow)),
    )
    c.commit()


def latest() -> Optional[Measurement]:
    c = _conn()
    row = c.execute(
        "SELECT * FROM measurements ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    return _row_to_measurement(row) if row else None


def query(since: float, until: float, limit: int = 1000) -> list[Measurement]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM measurements WHERE ts >= ? AND ts <= ? ORDER BY ts DESC LIMIT ?",
        (since, until, limit),
    ).fetchall()
    return [_row_to_measurement(r) for r in rows]


def query_last_seconds(seconds: int, limit: int = 500) -> list[Measurement]:
    return query(time.time() - seconds, time.time(), limit)


def stats(since: float) -> dict:
    c = _conn()
    row = c.execute(
        "SELECT MIN(level_db) AS min_db, MAX(level_db) AS max_db, "
        "AVG(level_db) AS avg_db, COUNT(*) AS count "
        "FROM measurements WHERE ts >= ?",
        (since,),
    ).fetchone()
    if row and row["count"]:
        return {
            "min_db": round(row["min_db"], 1),
            "max_db": round(row["max_db"], 1),
            "avg_db": round(row["avg_db"], 1),
            "count": row["count"],
        }
    return {"min_db": None, "max_db": None, "avg_db": None, "count": 0}


def query_aggregate(since: float, until: float, bucket_seconds: float) -> list[dict]:
    """Return time-bucketed LAeq/min/max for the given window.

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
        WHERE ts BETWEEN ? AND ?
        GROUP BY CAST(ts / ? AS INTEGER)
        ORDER BY bucket_ts
        """,
        (bucket_seconds, bucket_seconds, since, until, bucket_seconds),
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


def query_daily_levels(since: float, until: float) -> list[dict]:
    """Return per-calendar-day L_Tag, L_Nacht, and L_DEN for the given range.

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
        WHERE ts BETWEEN ? AND ?
        GROUP BY day
        ORDER BY day
        """,
        (since, until),
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


def query_hourly_profile(since: float, until: float) -> list[dict]:
    """Return energy-averaged LAeq per hour-of-day across all days in range."""
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
        WHERE ts BETWEEN ? AND ?
        GROUP BY hour
        ORDER BY hour
        """,
        (since, until),
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


def query_raw_export(since: float, until: float):
    """Return a cursor over (ts, level_db, weighting, response) in chronological order."""
    return _conn().execute(
        "SELECT ts, level_db, weighting, response FROM measurements "
        "WHERE ts BETWEEN ? AND ? ORDER BY ts",
        (since, until),
    )


def auth_get(key: str) -> Optional[str]:
    row = _conn().execute("SELECT value FROM auth WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def auth_set(key: str, value: str) -> None:
    c = _conn()
    c.execute("INSERT OR REPLACE INTO auth (key, value) VALUES (?, ?)", (key, value))
    c.commit()


def settings_get(ts: Optional[float] = None) -> Optional[Settings]:
    """Return the settings valid at time ts (default: now)."""
    if ts is None:
        ts = time.time()
    row = _conn().execute(
        "SELECT * FROM settings WHERE valid_from <= ? ORDER BY valid_from DESC LIMIT 1",
        (ts,),
    ).fetchone()
    return _row_to_settings(row) if row else None


def settings_list() -> list[Settings]:
    """Return all settings records ordered by valid_from ascending."""
    rows = _conn().execute(
        "SELECT * FROM settings ORDER BY valid_from ASC"
    ).fetchall()
    return [_row_to_settings(r) for r in rows]


def settings_save(s: Settings) -> int:
    """Insert a new settings record, return its id."""
    now = time.time()
    c = _conn()
    cur = c.execute(
        """
        INSERT INTO settings
          (created_at, valid_from, location_name, lat, lon, zone, limit_day, limit_night, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (now, s.valid_from, s.location_name, s.lat, s.lon,
         s.zone, s.limit_day, s.limit_night, s.comment),
    )
    c.commit()
    return cur.lastrowid


def settings_delete(id: int) -> bool:
    """Delete a settings record by id. Returns True if a row was deleted."""
    c = _conn()
    cur = c.execute("DELETE FROM settings WHERE id = ?", (id,))
    c.commit()
    return cur.rowcount > 0


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
