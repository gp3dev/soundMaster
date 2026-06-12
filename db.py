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


_local = threading.local()
_db_path: Optional[Path] = None


class _LeqAggregate:
    """SQLite user-defined aggregate: LAeq = 10·log10(⌀ 10^(L/10))."""
    def __init__(self) -> None:
        self._sum = 0.0
        self._n = 0

    def step(self, db: float) -> None:
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
