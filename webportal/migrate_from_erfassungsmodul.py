#!/usr/bin/env python3
"""
One-off migration: import an existing (pre-split) Erfassungsmodul SQLite database
into the Webportal's multi-station schema, tagging all rows with a single "default"
station.

Safe to re-run: uses INSERT OR IGNORE against the UNIQUE(station_id, ts) /
UNIQUE(station_id, valid_from) constraints, so repeated runs don't duplicate rows.

Usage:
  python migrate_from_erfassungsmodul.py \\
      --source ~/.local/share/soundmaster/measurements.db \\
      --target /data/webportal.db \\
      --station-name "Default" --station-slug default

To import many backups at once (e.g. across several stations), see migrate_batch.py.
"""

import argparse
import secrets
import sqlite3
import sys
from pathlib import Path

import auth
import db as database


def migrate_one(source_path: Path, target_path: Path, station_name: str, station_slug: str) -> dict:
    """Import one old-format SQLite DB into the Webportal DB under the given station.

    Assumes database.init(target_path) has already been called. Returns a dict with
    counts (and, if a new station was created, its freshly issued API key).
    """
    database.init(target_path)

    station = database.station_get_by_slug(station_slug)
    printed_key = None
    if station is None:
        raw_key = secrets.token_urlsafe(32)
        station = database.station_create(station_name, station_slug,
                                          auth.hash_password(raw_key))
        printed_key = raw_key
        print(f"Station '{station.name}' (slug={station.slug}) angelegt.")
    else:
        print(f"Station '{station.name}' (slug={station.slug}) existiert bereits — verwende sie.")

    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    # ── Measurements ─────────────────────────────────────────────────────────
    m_rows = src.execute(
        "SELECT ts, level_db, weighting, response, range_min, range_max, overflow, underflow "
        "FROM measurements ORDER BY ts"
    ).fetchall()
    measurements = [
        database.Measurement(
            ts=r["ts"], level_db=r["level_db"], weighting=r["weighting"] or "A",
            response=r["response"] or "SLOW", range_min=r["range_min"] or 30,
            range_max=r["range_max"] or 130, overflow=bool(r["overflow"]),
            underflow=bool(r["underflow"]),
        )
        for r in m_rows
    ]
    inserted = database.insert_many(measurements, station.id)
    print(f"Messwerte: {len(measurements)} in Quelle, {inserted} neu eingefügt "
          f"({len(measurements) - inserted} bereits vorhanden).")

    # ── Settings ─────────────────────────────────────────────────────────────
    try:
        s_rows = src.execute(
            "SELECT created_at, valid_from, location_name, lat, lon, zone, "
            "limit_day, limit_night, comment FROM settings ORDER BY valid_from"
        ).fetchall()
    except sqlite3.OperationalError:
        s_rows = []  # no settings table in source (already-trimmed Erfassungsmodul DB)

    s_before = len(database.settings_list(station.id))
    for r in s_rows:
        database.settings_save(station.id, database.Settings(
            valid_from=r["valid_from"], location_name=r["location_name"],
            lat=r["lat"], lon=r["lon"], zone=r["zone"],
            limit_day=r["limit_day"], limit_night=r["limit_night"], comment=r["comment"],
        ))
    s_after = len(database.settings_list(station.id))
    print(f"Einstellungen: {len(s_rows)} in Quelle, {s_after - s_before} neu eingefügt.")

    src.close()

    return {
        "source": str(source_path),
        "station_slug": station.slug,
        "measurements_total": len(measurements),
        "measurements_inserted": inserted,
        "settings_total": len(s_rows),
        "settings_inserted": s_after - s_before,
        "api_key": printed_key,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to the old combined-app SQLite DB")
    parser.add_argument("--target", required=True, help="Path to the Webportal SQLite DB")
    parser.add_argument("--station-name", default="Default", help="Display name for the station")
    parser.add_argument("--station-slug", default="default", help="URL slug for the station")
    args = parser.parse_args()

    source_path = Path(args.source).expanduser()
    target_path = Path(args.target).expanduser()
    if not source_path.exists():
        print(f"Fehler: Quelle nicht gefunden: {source_path}", file=sys.stderr)
        sys.exit(1)

    r = migrate_one(source_path, target_path, args.station_name, args.station_slug)

    print("\n(Die 'auth'-Tabelle der Quelle wird bewusst NICHT übernommen — "
          "das Webportal bootstrapt seinen eigenen Admin-Zugang.)")

    if r["api_key"]:
        print(f"\n{'=' * 60}")
        print(f"  API-Key für Station '{r['station_slug']}' (nur jetzt sichtbar): {r['api_key']}")
        print(f"  In config.toml [webportal] api_key des Erfassungsmoduls eintragen.")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
