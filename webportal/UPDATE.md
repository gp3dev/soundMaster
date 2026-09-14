# Webportal-Update (Docker)

Anleitung, um den Webportal-Container auf dem Server auf eine neue Code-Version zu
bringen. Setzt voraus: SSH-Zugriff auf den Server, Docker + Docker Compose
installiert, das Repo liegt dort als Git-Checkout vor.

Es gibt kein Registry-Image — `docker-compose.yml` baut mit `build: .` bei jedem
Deployment lokal aus dem Repo-Stand. Die SQLite-Datenbank liegt im benannten
Volume `webportal-data` (`/data/webportal.db` im Container) und bleibt bei einem
Rebuild/Neustart unangetastet.

## 1. Vorher: Datenbank sichern

Das Image basiert auf `python:3.13-slim` und enthält keine `sqlite3`-CLI, wohl
aber das Python-Modul `sqlite3` (Standardbibliothek). Backup daher über die
`backup()`-API von Python — funktioniert auch bei laufendem Schreibbetrieb
konsistent, ohne den Container anzuhalten:

```bash
cd /pfad/zum/repo/webportal
docker compose exec webportal python -c "
import sqlite3, datetime
name = f'/data/webportal-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}.db'
src = sqlite3.connect('/data/webportal.db')
dst = sqlite3.connect(name)
src.backup(dst)
dst.close(); src.close()
print(name)
"

# Backup zur Sicherheit auch auf den Host kopieren:
docker cp "$(docker compose ps -q webportal)":/data/ ./backups-$(date +%Y%m%d)/
```

## 2. Neuen Code holen

```bash
cd /pfad/zum/repo
git status              # keine lokalen Änderungen übersehen
git fetch origin
git pull origin main
```

## 3. Image neu bauen, Container ersetzen

```bash
cd webportal
docker compose up -d --build
```

- `--build` erzwingt den Rebuild mit dem neuen Code.
- `up -d` ersetzt nur den Container, das Volume `webportal-data` bleibt erhalten.
- Ist `SM_ADMIN_PASSWORD` nicht dauerhaft in einer `.env` neben `docker-compose.yml`
  hinterlegt, muss es beim Aufruf erneut mitgegeben werden (sonst wird bei einem
  Container ohne vorhandene DB ein neues Zufallspasswort generiert):
  ```bash
  SM_ADMIN_PASSWORD=<bestehendes Passwort> docker compose up -d --build
  ```

## 4. Verifizieren

```bash
docker compose ps                  # Status "healthy"
docker compose logs -f webportal   # Startlog prüfen
curl -f http://localhost:8080/healthz
```

Im Browser: Für eine bekannte Messstelle das Verlaufsdiagramm öffnen und prüfen,
dass weiterhin aktuelle Daten ankommen.

## 5. Aufräumen (optional)

```bash
docker image prune -f
```

## Rollback

```bash
git log --oneline -5        # gewünschten vorherigen Commit ermitteln
git checkout <commit-hash>
cd webportal
docker compose up -d --build
```

Die Datenbank im Volume ist davon nicht betroffen.

## Hinweise

- Kurzer Downtime-Moment beim Container-Neustart (Sekunden). Erfassungsmodule
  puffern währenddessen lokal und liefern automatisch nach.
- Reverse-Proxy/TLS (z. B. vor `laerm14a.diodora.de`) liegt außerhalb dieses
  Repos und ist von diesem Ablauf nicht betroffen.
