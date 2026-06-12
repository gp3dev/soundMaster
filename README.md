# SoundMaster

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Langzeit-Datenerfassung für den **Laserliner SoundTest-Master (082.070A)** Schallpegelmesser via USB-Seriell. Speichert Messwerte in SQLite, stellt eine REST-API bereit und zeigt Echtzeit-Daten in einer Terminal-Oberfläche (TUI) sowie einem Web-Dashboard an.

## Features

- **1-Hz-Empfang** über USB-Seriell (CP2102N, `/dev/ttyUSB0`, 2400 Baud 8N1)
- **Vollständig dekodiertes Protokoll** — dB-Wert, A/C-Gewichtung, SLOW/FAST-Zeitkonstante, Geräte-Uhr
- **SQLite-Datenbank** für persistente Langzeitaufzeichnung
- **REST-API** (FastAPI + uvicorn) mit aktuellen Werten, Verlauf und Statistiken
- **TUI** (Textual) mit Live-Graph und Statusanzeige
- **Web-Dashboard** (`http://localhost:8080`) mit Echtzeit-Diagramm

## Voraussetzungen

- Python 3.11+
- Laserliner SoundTest-Master angeschlossen an `/dev/ttyUSB0`
- Benutzer in der Gruppe `dialout` (`sudo usermod -aG dialout $USER`)

## Schnellstart

`start.sh` übernimmt alles auf einmal: Virtualenv anlegen, pip bootstrappen (funktioniert auch auf Debian ohne `ensurepip`), Abhängigkeiten installieren und die App starten.

```bash
./start.sh
```

Alle Argumente werden direkt an `main.py` weitergereicht:

```bash
./start.sh --port /dev/ttyUSB1
./start.sh --no-tui
./start.sh --probe
```

## Manuelle Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Verwendung

Nach aktiviertem venv (oder nach `./start.sh` für den Einmalstart):

```bash
# Normalbetrieb: TUI + API + Logging
.venv/bin/python main.py

# Anderen Port angeben
.venv/bin/python main.py --port /dev/ttyUSB0

# Ohne TUI (nur API + Logging, z. B. als Hintergrunddienst)
.venv/bin/python main.py --no-tui

# Protokoll-Diagnose: Rohbytes ausgeben
.venv/bin/python main.py --probe

# Weitere Optionen
.venv/bin/python main.py --baud 2400 --api-port 8080
```

## Konfiguration

Standardwerte in `config.toml`:

```toml
[serial]
port      = "/dev/ttyUSB0"
baud_rate = 2400
timeout   = 2.0

[database]
path = "~/.local/share/soundmaster/measurements.db"

[api]
host = "0.0.0.0"
port = 8080
```

## REST-API

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /current` | Aktuellste Messung |
| `GET /history?minutes=60` | Verlauf (auch `since=`, `until=`, `limit=`) |
| `GET /stats?minutes=60` | Min/Max/Avg für Zeitfenster |
| `GET /health` | Verbindungsstatus des Sensors |
| `GET /` | Web-Dashboard |
| `GET /live` | Echtzeit-Seite |
| `GET /settings` | Einstellungsseite (TA-Lärm-Grenzwerte) |

Beispiel-Antwort `/current`:

```json
{
  "ts": "2026-06-04T12:34:56Z",
  "ts_unix": 1749040496.0,
  "level_db": 38.5,
  "weighting": "A",
  "response": "SLOW",
  "range_min": 20,
  "range_max": 130,
  "overflow": false,
  "underflow": false
}
```

## Serielles Protokoll

Pakete sind 18 Byte lang, werden mit ~1 Hz gesendet.

| Byte | Inhalt |
|------|--------|
| 0 | `0xa0` / `0xa1` Marker (unteres Nibble: unbekanntes Statusbit) |
| 1 | Bit 3 (`0x08`): A(1)/C(0)-Gewichtung; Bit 2 (`0x04`): SLOW(1)/FAST(0); oberes Nibble `0x4x` beobachtet, Bedeutung unbekannt |
| 2 | `0x00` Konstante |
| 3–5 | dB-Wert: unteres Nibble = Zehner, Einer, Zehntel |
| 6–13 | Sync-Anker `00 00 00 01 00 01 00 00` |
| 14–17 | Geräte-Uhr BCD: HH MM SS_Zehner SS_Einer |

Bekannte Byte-1-Werte: `0x48` = A+FAST, `0x4c` = A+SLOW, `0x40` = C+FAST, `0x44` = C+SLOW.

Sync-Strategie: Suche den 8-Byte-Anker ab Offset 6, gehe 6 Bytes zurück für den Paketanfang.

> **Hinweis:** Range, Overflow und Underflow sind im Protokoll noch nicht dekodiert — `range_min/max` werden als 20/130 dB angenommen, `overflow`/`underflow` sind stets `false`.

## Projektstruktur

```
main.py              Einstiegspunkt, Thread-Orchestrierung
reader.py            Serieller Empfang & Protokoll-Dekodierung
api.py               FastAPI REST-API & statische Web-Dateien
db.py                SQLite-Datenbank (Lesen/Schreiben/Statistiken)
config.py            Konfigurationsladung (TOML + CLI-Argumente)
tui.py               Textual-TUI mit Live-Graph
web/
  index.html         Web-Dashboard (Verlaufsdiagramm)
  live.html          Echtzeit-Ansicht
  settings.html      Einstellungsseite (TA-Lärm-Grenzwerte)
  nav.css / nav.js   Navigation
config.toml          Standardkonfiguration
requirements.txt     Python-Abhängigkeiten
start.sh             Bootstrap-Skript (venv + pip + start)
```

## Lizenz

[MIT License](LICENSE) — Copyright (c) 2026 gp3dev
