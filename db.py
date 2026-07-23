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


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        assert _db_path is not None, "db.init() must be called first"
        _local.conn = sqlite3.connect(str(_db_path))
        _local.conn.row_factory = sqlite3.Row
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
    # settings/auth tables from older combined-app deployments may still exist in this
    # file — left untouched (not dropped) since they may hold data the admin still
    # wants around; they're simply unused by this codebase now.
    cols = {row["name"] for row in c.execute("PRAGMA table_info(measurements)")}
    if "synced_at" not in cols:
        c.execute("ALTER TABLE measurements ADD COLUMN synced_at REAL")
    c.execute("CREATE INDEX IF NOT EXISTS measurements_synced ON measurements(synced_at)")
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


def query_unsynced(limit: int = 200) -> list[tuple[int, Measurement]]:
    """Return (row_id, Measurement) pairs not yet forwarded to the Webportal, oldest first."""
    c = _conn()
    rows = c.execute(
        "SELECT * FROM measurements WHERE synced_at IS NULL ORDER BY ts ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(row["id"], _row_to_measurement(row)) for row in rows]


def mark_synced(ids: list[int]) -> None:
    """Mark the given row ids as successfully forwarded."""
    if not ids:
        return
    c = _conn()
    now = time.time()
    placeholders = ",".join("?" for _ in ids)
    c.execute(
        f"UPDATE measurements SET synced_at = ? WHERE id IN ({placeholders})",
        (now, *ids),
    )
    c.commit()


def prune_synced(retain_hours: float) -> int:
    """Delete already-synced rows older than retain_hours. Returns rows deleted."""
    c = _conn()
    cutoff = time.time() - retain_hours * 3600
    cur = c.execute(
        "DELETE FROM measurements WHERE synced_at IS NOT NULL AND ts < ?",
        (cutoff,),
    )
    c.commit()
    return cur.rowcount


def mark_all_synced_before(ts: float) -> int:
    """One-off cutover helper: mark every currently-unsynced row up to ts as synced
    (used after a manual historical-data migration, so the forwarder doesn't re-send
    the entire backlog)."""
    c = _conn()
    now = time.time()
    cur = c.execute(
        "UPDATE measurements SET synced_at = ? WHERE synced_at IS NULL AND ts <= ?",
        (now, ts),
    )
    c.commit()
    return cur.rowcount


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
