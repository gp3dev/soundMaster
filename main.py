#!/usr/bin/env python3
"""
SoundMaster — Langzeitdatenerfassung mit Laserliner SoundTest-Master

Verwendung:
  python main.py                          # Normalbetrieb (TUI + API + Logging)
  python main.py --probe                  # Rohbytes ausgeben (Protokoll ermitteln)
  python main.py --no-tui                 # Nur API + Logging (kein Terminal)
  python main.py --port /dev/ttyUSB0     # Seriellen Port angeben
  python main.py --baud 9600             # Baudrate ändern
  python main.py --api-port 8080         # API-Port ändern
"""

import queue
import signal
import sys
import threading
import time

import config
import db as database
import auth
from reader import SerialReader, probe, resolve_port
from api import ApiServer, set_connection_state


def _db_writer(data_queue: queue.Queue, stop_event: threading.Event) -> None:
    """Consumes measurements from the queue and writes them to the database."""
    while not stop_event.is_set() or not data_queue.empty():
        try:
            m = data_queue.get(timeout=0.5)
            database.insert(m)
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[db-writer] Error: {e}", file=sys.stderr)


def _run_no_tui(reader: SerialReader, stop_event: threading.Event) -> None:
    """Run without TUI — just log to DB and serve API until Ctrl-C."""
    print("SoundMaster running (no TUI). Press Ctrl-C to stop.")
    print(f"API: http://{cfg.api.host}:{cfg.api.port}/current")
    try:
        while not stop_event.is_set():
            connected = reader.connected
            set_connection_state(connected, reader.last_error)
            if not connected and reader.last_error:
                print(f"\r[serial] {reader.last_error}", end="", flush=True)
            elif connected:
                m = database.latest()
                if m:
                    print(f"\r{time.strftime('%H:%M:%S')}  {m.level_db:.1f} dB({m.weighting})  "
                          f"[{m.response}]         ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    print()


if __name__ == "__main__":
    cfg = config.load()

    # ── Probe mode ─────────────────────────────────────────────────────────
    if cfg.probe:
        probe(cfg.serial.port)
        sys.exit(0)

    # ── Port-Erkennung ─────────────────────────────────────────────────────
    cfg.serial.port = resolve_port(cfg.serial.port)

    # ── Diag mode ──────────────────────────────────────────────────────────
    if cfg.diag:
        from diag import run_diag
        run_diag(cfg.serial.port, cfg.db_path)
        sys.exit(0)

    # ── Normal mode ────────────────────────────────────────────────────────
    database.init(cfg.db_path)

    # ── Auth setup ─────────────────────────────────────────────────────────
    stored_hash = database.auth_get("password_hash")
    if stored_hash is None:
        import secrets as _sec
        import string as _str
        _alphabet = _str.ascii_letters + _str.digits
        _password = "".join(_sec.choice(_alphabet) for _ in range(16))
        stored_hash = auth.hash_password(_password)
        database.auth_set("password_hash", stored_hash)
        print(f"\n{'=' * 60}")
        print(f"  Admin-Passwort (nur einmalig angezeigt): {_password}")
        print(f"{'=' * 60}\n")
    auth.init(stored_hash)

    # Shared queue: reader → (tui_display + db_writer)
    # We use two separate queues so the TUI gets its own copy
    tui_queue: queue.Queue = queue.Queue(maxsize=500)
    db_queue: queue.Queue = queue.Queue(maxsize=500)

    proxy_queue = queue.Queue(maxsize=500)

    stop_event = threading.Event()

    # DB writer thread
    db_thread = threading.Thread(
        target=_db_writer, args=(db_queue, stop_event), daemon=True, name="db-writer"
    )
    db_thread.start()

    # Serial reader
    reader = SerialReader(
        port=cfg.serial.port,
        baud_rate=cfg.serial.baud_rate,
        timeout=cfg.serial.timeout,
        out_queue=proxy_queue,
    )

    # Fanout thread: proxy_queue → tui_queue + db_queue
    def _fanout():
        while not stop_event.is_set():
            try:
                item = proxy_queue.get(timeout=0.5)
                try:
                    tui_queue.put_nowait(item)
                except queue.Full:
                    pass
                try:
                    db_queue.put_nowait(item)
                except queue.Full:
                    pass
            except queue.Empty:
                continue

    fanout_thread = threading.Thread(target=_fanout, daemon=True, name="fanout")
    fanout_thread.start()

    reader.start()

    # API server
    api_server = ApiServer(host=cfg.api.host, port=cfg.api.port)
    api_server.start()

    # Keep API connection state in sync with reader
    def _status_updater():
        while not stop_event.is_set():
            set_connection_state(reader.connected, reader.last_error)
            time.sleep(2)

    status_thread = threading.Thread(target=_status_updater, daemon=True, name="status")
    status_thread.start()

    # ── TUI or headless ────────────────────────────────────────────────────
    if cfg.no_tui:
        def _handle_sigint(sig, frame):
            stop_event.set()

        signal.signal(signal.SIGINT, _handle_sigint)
        _run_no_tui(reader, stop_event)
    else:
        from tui import SoundMasterApp
        app = SoundMasterApp(data_queue=tui_queue, api_port=cfg.api.port)
        app.run()

    # ── Shutdown ───────────────────────────────────────────────────────────
    stop_event.set()
    reader.stop()
    api_server.stop()
    db_thread.join(timeout=3)
    print("Auf Wiedersehen.")
