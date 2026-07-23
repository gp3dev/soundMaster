"""Pushes buffered measurements to the Webportal, tolerating network outages.

Runs as a daemon thread. Reads unsynced rows from the local SQLite buffer
(db.py), POSTs them in batches to the Webportal's ingest endpoint, and marks
them synced on success. On failure it retries with exponential backoff,
leaving the rows in place so nothing is lost during an outage.
"""

import sys
import threading
import time

import requests

import db as database
from config import WebportalConfig


class Forwarder:
    def __init__(self, cfg: WebportalConfig, stop_event: threading.Event) -> None:
        self._cfg = cfg
        self._stop_event = stop_event
        self._thread = threading.Thread(target=self._run, daemon=True, name="forwarder")

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = None) -> None:
        # stop_event is shared/set by the caller before calling join().
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        backoff = self._cfg.interval
        last_prune = 0.0
        while not self._stop_event.is_set():
            batch = database.query_unsynced(self._cfg.batch_size)
            if not batch:
                now = time.time()
                if now - last_prune > 60:
                    database.prune_synced(self._cfg.retain_hours)
                    last_prune = now
                time.sleep(self._cfg.interval)
                continue

            payload = {
                "measurements": [
                    {
                        "ts": m.ts,
                        "level_db": m.level_db,
                        "weighting": m.weighting,
                        "response": m.response,
                        "range_min": m.range_min,
                        "range_max": m.range_max,
                        "overflow": m.overflow,
                        "underflow": m.underflow,
                    }
                    for _, m in batch
                ]
            }
            try:
                resp = requests.post(
                    f"{self._cfg.url.rstrip('/')}/api/ingest/measurements",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._cfg.api_key}"},
                    timeout=self._cfg.timeout,
                )
                if resp.status_code == 200:
                    database.mark_synced([row_id for row_id, _ in batch])
                    backoff = self._cfg.interval
                else:
                    print(f"[forwarder] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
                    self._sleep_backoff(backoff)
                    backoff = min(backoff * 2, self._cfg.backoff_max)
            except requests.RequestException as e:
                print(f"[forwarder] {e}", file=sys.stderr)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._cfg.backoff_max)

    def _sleep_backoff(self, seconds: float = None) -> None:
        wait = seconds if seconds is not None else self._cfg.interval
        self._stop_event.wait(timeout=wait)
