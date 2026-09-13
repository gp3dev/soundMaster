#!/usr/bin/env python3
"""
Batch-Import mehrerer alter Erfassungsmodul-Backup-Datenbanken ins Webportal.

Liest eine Manifest-CSV mit den Spalten `source,station_name,station_slug` und
importiert jede Zeile nacheinander per migrate_from_erfassungsmodul.migrate_one().
Dedup läuft wie beim Einzel-Import über UNIQUE(station_id, ts) — mehrfaches
Ausführen, überlappende Zeiträume oder ein bereits laufender Live-Sync für dieselbe
Station sind unbedenklich, es werden nie doppelte Messwerte eingefügt.

Manifest-Beispiel (migrations.csv):
    source,station_name,station_slug
    /tmp/backup_maerz.db,Messstelle Nord,messstelle-nord
    /tmp/backup_april.db,Messstelle Nord,messstelle-nord
    /tmp/backup_alt_ohne_zuordnung.db,Unzugeordnet,unzugeordnet

Mehrere Zeilen mit demselben station_slug sind normal (z. B. mehrere Backups
derselben Messstelle aus verschiedenen Monaten) — die Station wird beim ersten
Treffer angelegt und danach einfach weiterverwendet.

Usage:
    python migrate_batch.py --manifest migrations.csv --target /data/webportal.db
"""

import argparse
import csv
import sys
from pathlib import Path

import db as database
from migrate_from_erfassungsmodul import migrate_one


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--manifest", required=True,
                         help="CSV mit Spalten: source,station_name,station_slug")
    parser.add_argument("--target", required=True, help="Pfad zur Webportal-SQLite-DB")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser()
    target_path = Path(args.target).expanduser()
    if not manifest_path.exists():
        print(f"Fehler: Manifest nicht gefunden: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    database.init(target_path)

    results = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            source = (row.get("source") or "").strip()
            station_name = (row.get("station_name") or "").strip()
            station_slug = (row.get("station_slug") or "").strip()
            if not source:
                continue
            print(f"\n{'=' * 70}\n{source}  ->  {station_slug}\n{'=' * 70}")

            source_path = Path(source).expanduser()
            if not source_path.exists():
                print("  Fehler: Quelle nicht gefunden — übersprungen.", file=sys.stderr)
                results.append({"source": source, "station_slug": station_slug,
                                 "error": "Quelle nicht gefunden"})
                continue
            if not station_slug:
                print("  Fehler: station_slug fehlt — übersprungen.", file=sys.stderr)
                results.append({"source": source, "station_slug": station_slug,
                                 "error": "station_slug fehlt"})
                continue

            try:
                r = migrate_one(source_path, target_path, station_name or station_slug, station_slug)
            except Exception as e:
                print(f"  Fehler: {e}", file=sys.stderr)
                results.append({"source": source, "station_slug": station_slug, "error": str(e)})
                continue

            print(f"  Messwerte: {r['measurements_total']} in Quelle, "
                  f"{r['measurements_inserted']} neu eingefügt "
                  f"({r['measurements_total'] - r['measurements_inserted']} bereits vorhanden).")
            print(f"  Einstellungen: {r['settings_total']} in Quelle, "
                  f"{r['settings_inserted']} neu eingefügt.")
            if r["api_key"]:
                print(f"  NEUE Station '{r['station_slug']}' angelegt — "
                      f"API-Key (nur jetzt sichtbar): {r['api_key']}")
            results.append(r)

    print(f"\n{'=' * 70}\nZusammenfassung\n{'=' * 70}")
    total_inserted = 0
    had_error = False
    for r in results:
        if "error" in r:
            had_error = True
            print(f"  FEHLER  {r['source']:50s} {r['error']}")
        else:
            total_inserted += r["measurements_inserted"]
            print(f"  OK      {r['source']:50s} +{r['measurements_inserted']:>8} Messwerte "
                  f"-> {r['station_slug']}")
    print(f"\nGesamt neu eingefügt: {total_inserted} Messwerte")
    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
