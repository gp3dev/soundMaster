"""
Serial reader for Laserliner SoundTest-Master (082.070A).

Protocol identified via --probe + live capture (2026-06-04):
  Baud: 2400, 8N1
  Packet: 18 bytes at 1 Hz, starts with 0xa0 or 0xa1

  Byte  0:    0xa0 / 0xa1  packet marker (low nibble = unknown status bit)
  Byte  1:    bit 3 (0x08) = A-weighting (set) / C-weighting (clear)
              bit 2 (0x04) = SLOW (set) / FAST (clear)
              known values: 0x48=A+FAST, 0x4c=A+SLOW, 0x40=C+FAST, 0x44=C+SLOW
  Byte  2:    0x00  constant
  Byte  3:    low nibble = TENS digit of dB (0-9)
  Byte  4:    low nibble = ONES digit of dB (0-9)
  Byte  5:    low nibble = TENTHS digit of dB (0-9)
  Byte  6-9:  00 00 00 01  constant
  Byte 10-11: 00 01  constant
  Byte 12-13: 00 00  constant
  Byte 14:    BCD HH (device clock hours)
  Byte 15:    BCD MM (device clock minutes)
  Byte 16:    tens digit of seconds (low nibble)
  Byte 17:    units digit of seconds (low nibble)

  Example: a0 4c 00 03 06 09 00 00 00 01 00 01 00 00 01 06 02 09
           → 36.9 dB(A) at device clock 01:06:29

  Weighting: A confirmed; C-weighting encoding not yet observed.
  Response/range: encoding not yet identified.
"""

import queue
import threading
import time

import serial

from db import Measurement

PROBE_BAUDS = [2400, 4800, 9600, 19200, 38400]
PROBE_SECONDS = 4

_PACKET_SIZE = 18
# Constant bytes at positions 6-13 used as sync anchor
_SYNC_ANCHOR = bytes([0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00])
_SYNC_OFFSET = 6  # anchor starts at byte 6 within the 18-byte packet


def probe(port: str) -> None:
    """Dump raw serial bytes at each common baud rate to help identify the protocol."""
    print(f"Probing {port} — device must be in SENDING mode\n")
    for baud in PROBE_BAUDS:
        print(f"{'─'*60}")
        print(f"Baud rate: {baud}  ({PROBE_SECONDS}s)")
        print(f"{'─'*60}")
        buf = bytearray()
        try:
            with serial.Serial(port, baud, timeout=0.2) as s:
                s.reset_input_buffer()
                deadline = time.monotonic() + PROBE_SECONDS
                while time.monotonic() < deadline:
                    chunk = s.read(64)
                    if chunk:
                        buf.extend(chunk)
                        _hex_dump(chunk)
        except serial.SerialException as e:
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
                                   timeout=self._timeout) as s:
                    self._connected = True
                    self._last_error = ""
                    s.reset_input_buffer()
                    buf = bytearray()

                    while not self._stop.is_set():
                        chunk = s.read(32)
                        if chunk:
                            buf.extend(chunk)
                            self._drain_packets(buf)

            except serial.SerialException as e:
                self._connected = False
                self._last_error = str(e)
                if not self._stop.is_set():
                    time.sleep(2)

    def _drain_packets(self, buf: bytearray) -> None:
        """Extract complete 18-byte packets using the 8-byte sync anchor at offset 6."""
        while True:
            # Find the constant 8-byte anchor that sits at offset 6 in every packet
            idx = bytes(buf).find(_SYNC_ANCHOR)
            if idx == -1:
                # Keep last (anchor_len - 1) bytes — partial anchor may be in transit
                keep = len(_SYNC_ANCHOR) - 1
                if len(buf) > keep:
                    del buf[:len(buf) - keep]
                return

            # Anchor found at idx; packet starts _SYNC_OFFSET bytes before it
            pkt_start = idx - _SYNC_OFFSET
            if pkt_start < 0:
                # Anchor arrived but we're missing the leading bytes; discard and wait
                del buf[:idx + len(_SYNC_ANCHOR)]
                return

            if len(buf) - pkt_start < _PACKET_SIZE:
                # Full packet not yet in buffer
                if pkt_start > 0:
                    del buf[:pkt_start]
                return

            pkt = bytes(buf[pkt_start:pkt_start + _PACKET_SIZE])
            m = _decode_packet(pkt)
            if m is not None:
                self._q.put(m)
            del buf[:pkt_start + _PACKET_SIZE]


def _decode_packet(pkt: bytes) -> Measurement | None:
    """Decode an 18-byte Laserliner SoundTest-Master packet."""
    if len(pkt) < _PACKET_SIZE:
        return None

    # Marker byte must be 0xa0 or 0xa1
    if pkt[0] not in (0xa0, 0xa1):
        return None

    # Constant anchor at bytes 6-13
    if bytes(pkt[6:14]) != _SYNC_ANCHOR:
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
