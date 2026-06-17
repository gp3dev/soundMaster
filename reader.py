"""
Serial reader for Laserliner SoundTest-Master (082.070A).

Protocol identified via --probe + live capture (2026-06-04):
  Baud: 2400, 8N1
  Packet: 18 bytes at 1 Hz, starts with 0xa0 or 0xa1

  Byte  0:    0xa0 / 0xa1  packet marker (low nibble = unknown status bit)
  Byte  1:    bit 3 (0x08) = A-weighting (set) / C-weighting (clear)
              bit 2 (0x04) = SLOW (set) / FAST (clear)
              bit 6 (0x40) = unknown mode flag (set in some sessions, clear in others)
              observed: 0x48=A+FAST, 0x4c=A+SLOW, 0x40=C+FAST, 0x44=C+SLOW,
                        0x08=A+FAST(mode2), 0x0c=A+SLOW(mode2)
  Byte  2:    0x00  constant
  Byte  3:    low nibble = TENS digit of dB (0-9)
  Byte  4:    low nibble = ONES digit of dB (0-9)
  Byte  5:    low nibble = TENTHS digit of dB (0-9)
  Byte  6-13: mode-dependent status bytes — NOT constant across device modes.
              mode1 (byte1=0x4c): 00 00 00 01 00 01 00 00
              mode2 (byte1=0x08): 00 00 02 01 00 02 00 08
              bytes 6-7 and 9-10 appear stable (00 00 ?? 01 00 ??);
              bytes 8, 11, 13 vary with device mode/range.
  Byte 14:    BCD HH (device clock hours)
  Byte 15:    BCD MM (device clock minutes)
  Byte 16:    tens digit of seconds (low nibble)
  Byte 17:    units digit of seconds (low nibble)

  Example: a0 4c 00 03 06 09 00 00 00 01 00 01 00 00 01 06 02 09
           → 36.9 dB(A) at device clock 01:06:29

  Sync strategy: scan for marker byte 0xa0/0xa1 at byte 0; bytes 6-13 are
  NOT a reliable anchor because they differ between device modes.

  Weighting: A confirmed; C-weighting encoding not yet observed.
  Response/range: encoding not yet identified.
"""

import queue
import threading
import time

import serial
from serial.tools.list_ports import comports

from db import Measurement

_CP210X_VID = 0x10C4
_CP210X_PID = 0xEA60


def list_serial_ports():
    """Return all available serial ports sorted by device name."""
    return sorted(comports(), key=lambda p: p.device)


def resolve_port(configured_port: str) -> str:
    """Return the serial port to use, prompting for selection if needed.

    - Configured port exists and has a CP210x adapter → use it silently.
    - Exactly one CP210x found elsewhere → auto-select and print a notice.
    - Otherwise → print a numbered menu and wait for user input.
    """
    ports = list_serial_ports()
    devices = {p.device: p for p in ports}

    if configured_port in devices and devices[configured_port].vid == _CP210X_VID:
        return configured_port

    cp210x_ports = [p for p in ports if p.vid == _CP210X_VID]

    if len(cp210x_ports) == 1:
        found = cp210x_ports[0]
        print(f"CP2102N-Adapter gefunden: {found.device} ({found.description}) — wird verwendet.")
        return found.device

    if configured_port not in devices:
        print(f"Kein CP2102N-Adapter am konfigurierten Port {configured_port} gefunden.")
    else:
        print(f"Am konfigurierten Port {configured_port} kein CP2102N-Adapter erkannt.")

    if not ports:
        print(f"Warnung: Keine seriellen Ports gefunden. Verwende {configured_port}.")
        return configured_port

    print("\nVerfügbare serielle Ports:")
    for i, p in enumerate(ports, start=1):
        vid_pid = f"[{p.vid:04X}:{p.pid:04X}]" if p.vid is not None else ""
        desc = p.description or "(keine Beschreibung)"
        print(f"  [{i}] {p.device:<16} {desc:<40} {vid_pid}")

    default = 1
    while True:
        try:
            raw = input(f"\nPort auswählen [{default}]: ").strip()
            idx = int(raw) if raw else default
            if 1 <= idx <= len(ports):
                return ports[idx - 1].device
        except (ValueError, KeyboardInterrupt):
            pass
        print(f"Bitte eine Zahl zwischen 1 und {len(ports)} eingeben.")

PROBE_BAUDS = [2400, 4800, 9600, 19200, 38400]
PROBE_SECONDS = 4

_PACKET_SIZE = 18
# Reopen port if no bytes received for this long (catches silent USB driver stalls)
_WATCHDOG_SECONDS = 15


def probe(port: str) -> None:
    """Dump raw serial bytes at each common baud rate to help identify the protocol."""
    print(f"Probing {port} — device must be in SENDING mode\n")
    for baud in PROBE_BAUDS:
        print(f"{'─'*60}")
        print(f"Baud rate: {baud}  ({PROBE_SECONDS}s)")
        print(f"{'─'*60}")
        buf = bytearray()
        try:
            with serial.Serial(port, baud, timeout=0.2,
                               dsrdtr=False, rtscts=False) as s:
                s.reset_input_buffer()
                deadline = time.monotonic() + PROBE_SECONDS
                while time.monotonic() < deadline:
                    chunk = s.read(64)
                    if chunk:
                        buf.extend(chunk)
                        _hex_dump(chunk)
        except (serial.SerialException, OSError) as e:
            print(f"  Error: {e}")
        if buf:
            print(f"\n  Total bytes in {PROBE_SECONDS}s: {len(buf)}")
            print(f"  Bytes/sec ≈ {len(buf)/PROBE_SECONDS:.0f}  "
                  f"(expect ~{baud//100} for 11-byte packets)")
        else:
            print("  (no data received)")
        print()


def _hex_dump(data: bytes) -> None:
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04x}  {hex_part:<47}  {asc_part}")


class SerialReader:
    """Reads from serial port in a background thread, decodes packets,
    and puts Measurement objects into a queue."""

    def __init__(self, port: str, baud_rate: int, timeout: float,
                 out_queue: queue.Queue) -> None:
        self._port = port
        self._baud = baud_rate
        self._timeout = timeout
        self._q = out_queue
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="serial-reader")
        self._connected = False
        self._last_error: str = ""
        self._last_emitted: float = 0.0  # monotonic time of last packet forwarded

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with serial.Serial(self._port, self._baud,
                                   timeout=self._timeout,
                                   dsrdtr=False, rtscts=False) as s:
                    self._connected = True
                    self._last_error = ""
                    s.reset_input_buffer()
                    buf = bytearray()
                    last_rx = time.monotonic()

                    while not self._stop.is_set():
                        chunk = s.read(32)
                        now = time.monotonic()
                        if chunk:
                            last_rx = now
                            buf.extend(chunk)
                            self._drain_packets(buf)
                        elif now - last_rx > _WATCHDOG_SECONDS:
                            # No bytes for too long — silent USB driver stall; reopen port
                            self._connected = False
                            self._last_error = (
                                f"Keine Daten seit {_WATCHDOG_SECONDS}s — Port wird neu geöffnet"
                            )
                            break

            except (serial.SerialException, OSError) as e:
                self._connected = False
                self._last_error = str(e)
                if not self._stop.is_set():
                    time.sleep(2)

    def _drain_packets(self, buf: bytearray) -> None:
        """Extract complete 18-byte packets by scanning for the 0xa0/0xa1 marker byte.

        Bytes 6-13 differ between device modes and cannot be used as a reliable
        anchor, so we sync on byte 0 (0xa0/0xa1) and validate the packet content.
        """
        while len(buf) >= _PACKET_SIZE:
            found = False
            for i in range(len(buf) - _PACKET_SIZE + 1):
                if buf[i] not in (0xa0, 0xa1):
                    continue
                pkt = bytes(buf[i:i + _PACKET_SIZE])
                m = _decode_packet(pkt)
                if m is not None:
                    del buf[:i + _PACKET_SIZE]
                    now = time.monotonic()
                    if now - self._last_emitted >= 0.8:
                        self._q.put(m)
                        self._last_emitted = now
                    found = True
                    break
            if not found:
                # Keep the last (PACKET_SIZE - 1) bytes so a packet that spans
                # two reads can be completed on the next call.
                if len(buf) > _PACKET_SIZE - 1:
                    del buf[:len(buf) - (_PACKET_SIZE - 1)]
                return


def _decode_packet(pkt: bytes) -> Measurement | None:
    """Decode an 18-byte Laserliner SoundTest-Master packet."""
    if len(pkt) < _PACKET_SIZE:
        return None

    # Marker byte must be 0xa0 or 0xa1
    if pkt[0] not in (0xa0, 0xa1):
        return None

    # Byte 2 is always 0x00; acts as a lightweight sanity check
    if pkt[2] != 0x00:
        return None

    # dB value: low nibbles of bytes 3, 4, 5 = tens, ones, tenths
    tens   = pkt[3] & 0x0f
    ones   = pkt[4] & 0x0f
    tenths = pkt[5] & 0x0f
    if any(v > 9 for v in (tens, ones, tenths)):
        return None

    level_db = round(tens * 10 + ones + tenths * 0.1, 1)
    if not (20.0 <= level_db <= 130.0):
        return None

    # Device clock sanity check (bytes 14-17 = HH MM SS_tens SS_units)
    hh = (pkt[14] >> 4) * 10 + (pkt[14] & 0x0f)
    mm = (pkt[15] >> 4) * 10 + (pkt[15] & 0x0f)
    ss = (pkt[16] & 0x0f) * 10 + (pkt[17] & 0x0f)
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None

    weighting = "A" if (pkt[1] & 0x08) else "C"
    response  = "SLOW" if (pkt[1] & 0x04) else "FAST"

    return Measurement(
        ts=time.time(),
        level_db=level_db,
        weighting=weighting,
        response=response,
        range_min=20,
        range_max=130,
        overflow=False,
        underflow=False,
    )
