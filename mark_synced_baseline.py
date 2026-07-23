#!/usr/bin/env python3
"""
One-off cutover helper: marks every currently-unsynced row in the Erfassungsmodul's
local buffer DB as already synced, as of now.

Run this once, right after migrating historical data into the Webportal via
webportal/migrate_from_erfassungsmodul.py, and before pointing the Erfassungsmodul's
forwarder at the Webportal for the first time. Otherwise the forwarder would try to
re-send the entire historical backlog, which is redundant (and would race with /
duplicate the one-off migration).

Usage:
  python mark_synced_baseline.py --db ~/.local/share/soundmaster/measurements.db
"""

import argparse
import time
from pathlib import Path

import db as database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the Erfassungsmodul's local SQLite DB")
    args = parser.parse_args()

    database.init(Path(args.db).expanduser())
    n = database.mark_all_synced_before(time.time())
    print(f"{n} Zeilen als bereits synchronisiert markiert.")


if __name__ == "__main__":
    main()
