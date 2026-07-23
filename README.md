# SoundMaster

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Langzeit-Datenerfassung für den **Laserliner SoundTest-Master (082.070A)** Schallpegelmesser via USB-Seriell. Das Projekt besteht aus zwei getrennt deploybaren Komponenten:

- **Erfassungsmodul** (Repo-Root) — läuft direkt an der Messstelle, liest den Sensor über USB-Seriell aus, zeigt die Live-Werte in einer Terminal-Oberfläche (TUI) und einer schlanken lokalen Web-Seite an, und überträgt die Messwerte per HTTP an ein zentrales Webportal.
- **Webportal** (`webportal/`) — läuft als eigenständiger Docker-Container auf einem beliebigen Server, nimmt Messwerte von einer oder mehreren Messstellen entgegen und stellt die komplette Auswertung (Verlauf, Kalender, Tages-Pegel, Export, Karte, Berichte) sowie die Stationsverwaltung bereit.

```
Messstelle 1 ──┐
Messstelle 2 ──┼── HTTP Push (API-Key pro Station) ──▶  Webportal (Docker)
Messstelle N ──┘                                          │
                                                            └── Web-Dashboard, Kalender,
                                                                Export, Berichte, Stations-
                                                                verwaltung (Admin-Login)
```

Jede Messstelle puffert ihre Messwerte lokal in SQLite und liefert sie bei Verbindungsabbruch automatisch nach — es gehen keine Daten verloren, wenn das Webportal oder das Netzwerk kurzzeitig nicht erreichbar ist. Das Webportal selbst hat **keine Live-Ansicht** — die gehört ausschließlich zum Erfassungsmodul vor Ort.

---

## Erfassungsmodul

### Features

- **1-Hz-Empfang** über USB-Seriell (CP2102N, `/dev/ttyUSB0`, 2400 Baud 8N1)
- **Vollständig dekodiertes Protokoll** — dB-Wert, A/C-Gewichtung, SLOW/FAST-Zeitkonstante, Geräte-Uhr
- **TUI** (Textual) mit Live-Graph und Statusanzeige
- **Lokale Web-Live-Ansicht** (`http://<messstelle>:8080`) mit Echtzeit-Diagramm der letzten Stunde — unabhängig vom Webportal nutzbar
- **Lokaler SQLite-Puffer** — Messwerte werden dauerhaft lokal gespeichert, bis sie erfolgreich ans Webportal übertragen wurden
- **Forwarder** — überträgt Messwerte in Batches ans Webportal, mit exponentiellem Backoff bei Verbindungsproblemen; bereits übertragene Zeilen werden nach einer Aufbewahrungsfrist automatisch aufgeräumt
- Läuft auch komplett **stand-alone** ohne Webportal (leere `[webportal] url` in der Konfiguration)

### Voraussetzungen

- Python 3.11+
- Laserliner SoundTest-Master angeschlossen an `/dev/ttyUSB0`
- Benutzer in der Gruppe `dialout` (`sudo usermod -aG dialout $USER`)

### Schnellstart

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

### Manuelle Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Verwendung

Nach aktiviertem venv (oder nach `./start.sh` für den Einmalstart):

```bash
# Normalbetrieb: TUI + lokale API + Logging (+ Übertragung ans Webportal, falls konfiguriert)
.venv/bin/python main.py

# Anderen Port angeben
.venv/bin/python main.py --port /dev/ttyUSB0

# Ohne TUI (nur lokale API + Logging + Forwarder, z. B. als Hintergrunddienst)
.venv/bin/python main.py --no-tui

# Protokoll-Diagnose: Rohbytes ausgeben (kein DB-Schreiben, kein API)
.venv/bin/python main.py --probe

# Diagnosemodus: Rohdaten dekodieren + in DB schreiben, ohne TUI/API
.venv/bin/python main.py --diag

# Webportal-Anbindung direkt per CLI überschreiben
.venv/bin/python main.py --webportal-url https://noise.example.org --webportal-key <API-Key>

# Weitere Optionen
.venv/bin/python main.py --baud 2400 --api-port 8080
```

### Konfiguration

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

[webportal]
# Leer lassen, um die Übertragung ans Webportal zu deaktivieren (Stand-alone-Betrieb).
url          = ""
api_key      = ""
batch_size   = 200
interval     = 10.0
timeout      = 5.0
backoff_max  = 300.0
retain_hours = 2.0
```

`url`/`api_key` werden beim Anlegen einer Messstelle im Webportal generiert (siehe unten, Abschnitt „Erste Einrichtung“). Solange `url` leer ist, läuft das Erfassungsmodul komplett eigenständig und puffert nur lokal.

### Lokale REST-API

Rein lesend, keine Authentifizierung — dient ausschließlich der lokalen Live-Ansicht (und z. B. einer lokalen Home-Assistant-Integration direkt an der Messstelle):

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /health` | Verbindungsstatus des Sensors |
| `GET /current` | Aktuellste Messung im lokalen Puffer |
| `GET /history?minutes=60` | Rohverlauf aus dem lokalen Puffer (auch `since=`, `until=`, `limit=`) |

Die vollständige Historie, Statistiken, Export und Einstellungsverwaltung leben ausschließlich im Webportal (siehe unten).

### Serielles Protokoll

Pakete sind 18 Byte lang, werden mit ~1 Hz gesendet.

| Byte | Inhalt |
|------|--------|
| 0 | `0xa0` / `0xa1` Marker (unteres Nibble: unbekanntes Statusbit) |
| 1 | Bit 3 (`0x08`): A(1)/C(0)-Gewichtung; Bit 2 (`0x04`): SLOW(1)/FAST(0); Bit 6 (`0x40`): unbekanntes Modusflag |
| 2 | `0x00` Konstante |
| 3–5 | dB-Wert: unteres Nibble = Zehner, Einer, Zehntel |
| 6–11 | Invarianter Sync-Anker `00 00 00 01 00 01` |
| 12–13 | Variable Statusbytes (`0x00 0x00` oder `0x00 0x01` beobachtet) |
| 14–17 | Geräte-Uhr BCD: HH MM SS_Zehner SS_Einer |

Bekannte Byte-1-Werte: `0x48` = A+FAST, `0x4c` = A+SLOW, `0x40` = C+FAST, `0x44` = C+SLOW, `0x08` = A+FAST (Modus 2), `0x0c` = A+SLOW (Modus 2).

Sync-Strategie: Suche den 6-Byte-Anker ab Offset 6, gehe 6 Bytes zurück für den Paketanfang. Bytes 12–13 werden nicht für die Sync-Erkennung verwendet, da sie gerätezustandsabhängig variieren.

> **Hinweis:** Range, Overflow und Underflow sind im Protokoll noch nicht dekodiert — `range_min/max` werden als 30/130 dB angenommen, `overflow`/`underflow` sind stets `false`.

### Projektstruktur (Erfassungsmodul)

```
main.py              Einstiegspunkt, Thread-Orchestrierung
reader.py             Serieller Empfang & Protokoll-Dekodierung
api.py                Schlanke lokale FastAPI: /health, /current, /history + Live-Seite
db.py                 Lokaler SQLite-Puffer (Messwerte + Sync-Tracking)
forwarder.py          Überträgt gepufferte Messwerte ans Webportal (Batch, Backoff)
config.py             Konfigurationsladung (TOML + CLI-Argumente)
tui.py                Textual-TUI mit Live-Graph
diag.py               Diagnosemodus (--diag): Rohdaten + DB ohne TUI/API
mark_synced_baseline.py  Einmaliges Cutover-Skript (siehe Webportal-Abschnitt „Migration“)
web/
  index.html          Lokale Live-Ansicht (aktueller Wert + Graph der letzten Stunde)
  nav.css / nav.js     Theme-Umschalter
  theme-init.js        Theme-Vorladung (verhindert Flash beim Laden)
config.toml           Standardkonfiguration
requirements.txt      Python-Abhängigkeiten
start.sh              Bootstrap-Skript (venv + pip + start)
```

---

## Webportal

### Features

- **Mehrere Messstellen** — jede Station hat einen eigenen API-Key, eigene Standort-/Grenzwert-Einstellungen (zeitversioniert) und eine eigene Datenhistorie
- **Ingest-API** — nimmt per API-Key authentifizierte Messwert-Batches entgegen, idempotent (mehrfaches Senden derselben Werte erzeugt keine Duplikate)
- **Ein globaler Admin-Login** verwaltet alle Stationen (anlegen, API-Key widerrufen/erneuern) sowie die Standort-Einstellungen jeder Station
- **Web-Dashboard** mit Verlaufschart, Tagesganglinie, Kalender-Übersicht, Tages-Beurteilungspegeln (L_Tag/L_Nacht/L_DEN), CSV-Export, interaktiver Karte und druckbaren Berichten — je nach ausgewählter Station über einen Stations-Umschalter in der Navigation
- **Keine Live-Ansicht** — die lebt ausschließlich am Erfassungsmodul vor Ort
- **Docker-Deployment** — eigenständiges Image, Konfiguration über Umgebungsvariablen, SQLite-Datei in einem Volume

### Deployment (Docker Compose)

```bash
cd webportal
SM_ADMIN_PASSWORD=<dein-passwort> docker compose up -d --build
```

Ohne `SM_ADMIN_PASSWORD` wird beim ersten Start ein zufälliges Admin-Passwort generiert und einmalig in die Container-Logs geschrieben (`docker compose logs webportal`).

Umgebungsvariablen (`webportal/config.py`):

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `SM_HOST` | `0.0.0.0` | Bind-Adresse |
| `SM_PORT` | `8080` | Port |
| `SM_DB_PATH` | `/data/webportal.db` | Pfad zur SQLite-Datenbank (im Docker-Volume) |
| `SM_ADMIN_PASSWORD` | — | Admin-Passwort beim ersten Start; leer = zufällig generiert und geloggt |
| `SM_SESSION_TTL_HOURS` | `8` | Gültigkeitsdauer einer Admin-Session |

### Erste Einrichtung

1. Webportal deployen (siehe oben) und mit dem Admin-Passwort unter `/login` anmelden.
2. Unter `/stations` eine neue Messstelle anlegen — der angezeigte API-Key wird **nur einmal** angezeigt.
3. Am Erfassungsmodul in `config.toml` unter `[webportal]` `url` und `api_key` eintragen (oder per `--webportal-url`/`--webportal-key`), Erfassungsmodul neu starten.
4. Unter `/settings` (im Webportal, für die jeweils ausgewählte Station) Standort und TA-Lärm-Grenzwerte hinterlegen.

### REST-API

#### Ingest (für Erfassungsmodule)

| Endpunkt | Auth | Beschreibung |
|----------|------|-------------|
| `POST /api/ingest/measurements` | API-Key (`Authorization: Bearer <key>`) | Messwert-Batch entgegennehmen; Station wird ausschließlich aus dem Key abgeleitet |

#### Messdaten (öffentlich, jeweils mit Pflichtparameter `station=<slug>`)

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /history?station=<slug>&minutes=60` | Roher Verlauf (auch `since=`, `until=`, `limit=`) |
| `GET /history/aggregate?station=<slug>&minutes=60&buckets=200` | Aggregierter Verlauf: LAeq, min, max pro Zeitbucket |
| `GET /stats?station=<slug>&minutes=60` | Min/Max/Avg für Zeitfenster |
| `GET /stats/daily?station=<slug>` | L_Tag, L_Nacht, L_DEN pro Kalendertag (16. BImSchV / EU 2002/49/EG) |
| `GET /stats/hourly-profile?station=<slug>` | Energie-gemittelter LAeq pro Tagesstunde |
| `GET /export/csv?station=<slug>` | Rohmesswerte als CSV-Download |

#### Einstellungen (pro Station)

| Endpunkt | Auth | Beschreibung |
|----------|------|-------------|
| `GET /api/settings?station=<slug>` | — | Aktuelle Einstellungen (optional `?at=<ISO8601>` für historischen Stand) |
| `GET /api/settings/history?station=<slug>` | — | Alle Einstellungs-Perioden chronologisch |
| `POST /api/settings?station=<slug>` | ✓ | Neue Einstellungs-Periode anlegen |
| `DELETE /api/settings/{id}` | ✓ | Einstellungs-Eintrag löschen |

#### Stationsverwaltung

| Endpunkt | Auth | Beschreibung |
|----------|------|-------------|
| `GET /api/stations` | — | Öffentliche Liste aktiver Stationen (id, name, slug, `last_seen_at`) — treibt den Stations-Umschalter |
| `GET /api/stations/admin` | ✓ | Vollständige Liste inkl. widerrufener Stationen |
| `POST /api/stations` | ✓ | Neue Station anlegen — gibt den API-Key **einmalig** zurück |
| `POST /api/stations/{id}/revoke` | ✓ | API-Key sofort ungültig machen; Messdaten bleiben erhalten |
| `POST /api/stations/{id}/regenerate-key` | ✓ | Neuen API-Key erzeugen (alter wird ungültig) |

#### Authentifizierung & Sonstiges

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /auth/status` | Session-Status (`{"authenticated": bool}`) |
| `POST /auth/login` | Login (`{"password": "..."}`) — setzt Cookie `sm_session` |
| `POST /auth/logout` | Logout — löscht Cookie |
| `GET /healthz` | Docker-Liveness-Check (Prozess läuft) |

Vollständige, interaktive API-Dokumentation (automatisch aus dem Code generiert): **`/docs`** (Swagger UI) bzw. **`/redoc`**.

Beispiel-Antwort `GET /api/stations`:

```json
[
  { "id": 1, "name": "A6 Dechendorf", "slug": "a6-dechendorf", "last_seen_at": 1784836655.9 }
]
```

Beispiel-Antwort `GET /history?station=a6-dechendorf`:

```json
{
  "ts": "2026-06-04T12:34:56Z",
  "ts_unix": 1749040496.0,
  "level_db": 38.5,
  "weighting": "A",
  "response": "SLOW",
  "range_min": 30,
  "range_max": 130,
  "overflow": false,
  "underflow": false
}
```

### Migration bestehender Daten

Wer schon vor der Aufteilung Messdaten in der lokalen Datenbank des Erfassungsmoduls gesammelt hat, kann diese einmalig ins Webportal übernehmen:

```bash
# 1. Im Webportal: Bestandsdaten importieren und einer (neuen) Station zuordnen
cd webportal
python3 migrate_from_erfassungsmodul.py \
    --source ~/.local/share/soundmaster/measurements.db \
    --target /pfad/zur/webportal.db \
    --station-name "Default" --station-slug default

# 2. Am Erfassungsmodul: bereits migrierte Zeilen als "synced" markieren,
#    damit der Forwarder sie nicht erneut überträgt
cd ..
python3 mark_synced_baseline.py --db ~/.local/share/soundmaster/measurements.db

# 3. Am Erfassungsmodul: in config.toml [webportal] url/api_key eintragen und neu starten
```

Beide Skripte sind idempotent (mehrfaches Ausführen erzeugt keine Duplikate).

### Projektstruktur (Webportal)

```
webportal/
  main.py                          Einstiegspunkt: Config laden, Admin-Bootstrap, uvicorn starten
  config.py                        Env-Var-basierte Konfiguration
  db.py                            SQLite mit Mehrstationen-Schema (stations, measurements, settings, auth)
  auth.py                          Passwort-/API-Key-Hashing (PBKDF2-SHA256) & Session-Verwaltung
  api.py                           Stations-bezogene REST-Routen + statisches Frontend
  ingest.py                        POST /api/ingest/measurements (Stations-Key-Auth)
  stations_admin.py                Stationsverwaltung + /healthz
  migrate_from_erfassungsmodul.py  Einmaliges Migrationsskript für Bestandsdaten
  Dockerfile, docker-compose.yml   Container-Deployment
  web/
    index.html                    Verlaufsdiagramm (LAeq, min/max-Band) mit Stations-Umschalter
    profile.html                  Tagesganglinie (Stundenprofil)
    calendar.html                 Kalender-Übersicht
    daily.html                    Tages-Pegel (L_Tag / L_Nacht / L_DEN)
    export.html                   CSV-Export / Bericht
    map.html                      Interaktive Karte (Leaflet)
    settings.html                 Standort & Grenzwerte je Station (Login erforderlich)
    stations.html                 Stationsverwaltung: anlegen, API-Key widerrufen/erneuern (Login erforderlich)
    login.html                    Admin-Login
    report-print.html             Druckbarer Messbericht
    station.js                    Gemeinsamer Stations-Umschalter (Dropdown, localStorage)
    nav.css / nav.js / theme-init.js  Navigation & Theme-Umschalter
```

---

## Lizenz

[MIT License](LICENSE) — Copyright (c) 2026 gp3dev
