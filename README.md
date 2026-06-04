# SoundMaster

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

## Installation

```bash
pip install -r requirements.txt
```

## Verwendung

```bash
# Normalbetrieb: TUI + API + Logging
python main.py

# Anderen Port angeben
python main.py --port /dev/ttyUSB0

# Ohne TUI (nur API + Logging, z. B. als Hintergrunddienst)
python main.py --no-tui

# Protokoll-Diagnose: Rohbytes ausgeben
python main.py --probe

# Weitere Optionen
python main.py --baud 2400 --api-port 8080
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
| 0 | `0xa0` / `0xa1` Marker |
| 1 | Bit 3: A(1)/C(0)-Gewichtung; Bit 2: SLOW(1)/FAST(0) |
| 2 | `0x00` Konstante |
| 3–5 | dB-Wert: unteres Nibble = Zehner, Einer, Zehntel |
| 6–13 | Sync-Anker `00 00 00 01 00 01 00 00` |
| 14–17 | Geräte-Uhr BCD: HH MM SS_Zehner SS_Einer |

Sync-Strategie: Suche den 8-Byte-Anker ab Offset 6, gehe 6 Bytes zurück für den Paketanfang.

## Projektstruktur

```
main.py        Einstiegspunkt, Thread-Orchestrierung
reader.py      Serieller Empfang & Protokoll-Dekodierung
api.py         FastAPI REST-API & statische Web-Dateien
db.py          SQLite-Datenbank (Lesen/Schreiben/Statistiken)
config.py      Konfigurationsladung (TOML + CLI-Argumente)
tui.py         Textual-TUI mit Live-Graph
web/           Web-Dashboard (HTML/CSS/JS)
config.toml    Standardkonfiguration
```

## Lizenz

Privat / nicht veröffentlicht.
