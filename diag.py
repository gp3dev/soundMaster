"""
Diagnosemodul für SoundMaster — gibt Rohdaten im Terminal aus und speichert
empfangene Messungen in der Datenbank.

Verwendung: python main.py --diag [--port /dev/ttyUSB0]

Hilft dabei, USB-Stalls und Protokollfehler einzugrenzen, indem alle
empfangenen Bytes sofort mit Zeitstempel ausgegeben werden.
"""

import signal
import sys
import time

import serial

import db as database
from reader import (
    _PACKET_SIZE,
    _decode_packet,
    resolve_port,
)

_BAUD = 2400
_GAP_WARN = 2.0    # erste Pause-Meldung nach N Sekunden ohne Daten
_GAP_ERR  = 5.0    # zweite Warnung
_GAP_STALL = 15.0  # Watchdog → Port neu öffnen
_STATS_INTERVAL = 30.0

# ANSI-Farben
_C_RESET  = "\033[0m"
_C_DIM    = "\033[2m"
_C_YELLOW = "\033[33m"
_C_RED    = "\033[31m"
_C_GREEN  = "\033[32m"
_C_CYAN   = "\033[36m"
_C_BOLD   = "\033[1m"


def _ts() -> str:
    return time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"


def _log(msg: str, color: str = "") -> None:
    print(f"{_C_DIM}{_ts()}{_C_RESET} {color}{msg}{_C_RESET}", flush=True)


def _hex_dump(data: bytes, base_offset: int = 0) -> None:
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"         {_C_DIM}{base_offset + i:04x}{_C_RESET}  "
              f"{hex_part:<47}  {_C_DIM}{asc_part}{_C_RESET}", flush=True)


def _print_header(port: str, db_path) -> None:
    print(f"\n{_C_BOLD}{'═'*65}{_C_RESET}")
    print(f"{_C_BOLD}  SoundMaster Diagnosemodus{_C_RESET}")
    print(f"  Port : {port}  |  Baud: {_BAUD}")
    print(f"  DB   : {db_path}")
    print(f"  Start: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{_C_BOLD}{'═'*65}{_C_RESET}\n", flush=True)


def _print_stats(stats: dict) -> None:
    elapsed = time.time() - stats["session_start"]
    last_ok = stats["last_packet_ts"]
    ago = f"{time.time() - last_ok:.0f}s" if last_ok else "—"
    rate = stats["packets_decoded"] / max(elapsed, 1) * 60
    print(f"\n{_C_BOLD}── Statistik ({elapsed:.0f}s Laufzeit) {'─'*35}{_C_RESET}")
    print(f"  Bytes gesamt      : {stats['bytes_total']}")
    print(f"  Pakete dekodiert  : {stats['packets_decoded']}  "
          f"({rate:.1f}/min)")
    print(f"  Decode-Fehler     : {stats['packets_failed']}")
    print(f"  Sync-Treffer      : {stats['anchor_hits']}")
    print(f"  Port-Reconnects   : {stats['reconnects']}")
    print(f"  Letzte Messung vor: {ago}")
    print(f"{_C_BOLD}{'─'*50}{_C_RESET}\n", flush=True)


def _process_buf(buf: bytearray, stats: dict) -> None:
    """Extrahiert Pakete aus dem Buffer; loggt Marker-Treffer und Decode-Ergebnisse."""
    while len(buf) >= _PACKET_SIZE:
        found = False
        for i in range(len(buf) - _PACKET_SIZE + 1):
            if buf[i] not in (0xa0, 0xa1):
                continue

            stats["anchor_hits"] += 1
            pkt = bytes(buf[i:i + _PACKET_SIZE])

            # Paket hex-kompakt anzeigen (3 Gruppen: Header | Status | Timestamp)
            h = " ".join(f"{b:02x}" for b in pkt[0:6])
            s = " ".join(f"{b:02x}" for b in pkt[6:14])
            t = " ".join(f"{b:02x}" for b in pkt[14:18])
            _log(f"[PAKET]  {h}  |  {s}  |  {t}", _C_CYAN)

            m = _decode_packet(pkt)
            if m is not None:
                stats["packets_decoded"] += 1
                stats["last_packet_ts"] = time.time()
                _log(
                    f"[MESSUNG] {m.level_db:.1f} dB({m.weighting})  [{m.response}]"
                    f"  (#{stats['packets_decoded']})",
                    _C_GREEN,
                )
                try:
                    database.insert(m)
                    _log("[DB] gespeichert ✓", _C_DIM)
                except Exception as e:
                    _log(f"[DB-FEHLER] {e}", _C_RED)
                del buf[:i + _PACKET_SIZE]
                found = True
                break
            else:
                stats["packets_failed"] += 1
                reasons = []
                if pkt[0] not in (0xa0, 0xa1):
                    reasons.append(f"Marker=0x{pkt[0]:02x} (erwartet 0xa0/0xa1)")
                if pkt[2] != 0x00:
                    reasons.append(f"B2=0x{pkt[2]:02x} (erwartet 0x00)")
                tens, ones, tenths = pkt[3] & 0x0f, pkt[4] & 0x0f, pkt[5] & 0x0f
                if any(v > 9 for v in (tens, ones, tenths)):
                    reasons.append(
                        f"Ziffern-Nibble ungültig:"
                        f" B3=0x{pkt[3]:02x} B4=0x{pkt[4]:02x} B5=0x{pkt[5]:02x}"
                        f" → {tens}/{ones}/{tenths}"
                    )
                else:
                    level_db = round(tens * 10 + ones + tenths * 0.1, 1)
                    if not (20.0 <= level_db <= 130.0):
                        reasons.append(
                            f"Pegel außerhalb Bereich: {level_db} dB (erwartet 20–130)"
                        )
                hh = (pkt[14] >> 4) * 10 + (pkt[14] & 0x0f)
                mm = (pkt[15] >> 4) * 10 + (pkt[15] & 0x0f)
                ss = (pkt[16] & 0x0f) * 10 + (pkt[17] & 0x0f)
                if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                    reasons.append(
                        f"Uhrzeit ungültig: {hh:02d}:{mm:02d}:{ss:02d}"
                        f" (B14=0x{pkt[14]:02x} B15=0x{pkt[15]:02x}"
                        f" B16=0x{pkt[16]:02x} B17=0x{pkt[17]:02x})"
                    )
                reason_str = " | ".join(reasons) if reasons else "unbekannt"
                _log(f"[DECODE-FEHLER] {reason_str}", _C_RED)

        if not found:
            if len(buf) > _PACKET_SIZE - 1:
                del buf[:len(buf) - (_PACKET_SIZE - 1)]
            return


def run_diag(port: str, db_path) -> None:
    """Hauptfunktion für den Diagnosemodus."""
    database.init(db_path)

    stop_flag = [False]

    def _on_sigint(sig, frame):
        stop_flag[0] = True

    signal.signal(signal.SIGINT, _on_sigint)

    stats = {
        "bytes_total": 0,
        "packets_decoded": 0,
        "packets_failed": 0,
        "anchor_hits": 0,
        "reconnects": 0,
        "last_packet_ts": None,
        "session_start": time.time(),
    }

    _print_header(port, db_path)

    while not stop_flag[0]:
        try:
            with serial.Serial(port, _BAUD, timeout=0.5,
                               dsrdtr=False, rtscts=False) as s:
                _log(f"[VERBUNDEN] {port} @ {_BAUD} Baud", _C_GREEN)
                s.reset_input_buffer()
                buf = bytearray()
                byte_offset = 0
                last_rx = time.monotonic()
                last_stats = time.monotonic()
                warned_2s = warned_5s = False

                while not stop_flag[0]:
                    chunk = s.read(32)
                    now = time.monotonic()
                    gap = now - last_rx

                    if chunk:
                        if warned_2s or warned_5s:
                            _log(
                                f"[DATEN WIEDER DA] Pause war {gap:.1f}s  "
                                f"(Buffer enthielt {len(buf)} Restbytes)",
                                _C_GREEN,
                            )
                            warned_2s = warned_5s = False

                        last_rx = now
                        stats["bytes_total"] += len(chunk)

                        _log(
                            f"[RAW +{len(chunk)}B | gesamt: {stats['bytes_total']}B"
                            f" | buf: {len(buf)}B]"
                        )
                        _hex_dump(chunk, byte_offset)
                        byte_offset += len(chunk)

                        buf.extend(chunk)
                        _process_buf(buf, stats)

                    else:
                        if gap > _GAP_STALL:
                            _log(
                                f"[STALL] Keine Daten seit {gap:.1f}s"
                                " — Port wird neu geöffnet (Watchdog)",
                                _C_RED,
                            )
                            stats["reconnects"] += 1
                            break
                        elif gap > _GAP_ERR and not warned_5s:
                            _log(f"[WARNUNG] Keine Daten seit {gap:.1f}s", _C_YELLOW)
                            warned_5s = True
                        elif gap > _GAP_WARN and not warned_2s:
                            _log(f"[PAUSE] Keine Daten seit {gap:.1f}s", _C_YELLOW)
                            warned_2s = True

                    if now - last_stats >= _STATS_INTERVAL:
                        _print_stats(stats)
                        last_stats = now

        except (serial.SerialException, OSError) as e:
            _log(f"[FEHLER] {e}", _C_RED)
            stats["reconnects"] += 1
            if not stop_flag[0]:
                time.sleep(2)

    print()
    _print_stats(stats)
    _log("Diagnose beendet.")
