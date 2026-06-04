#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

VENV=".venv"

# Virtualenv anlegen falls nicht vorhanden
if [ ! -f "$VENV/bin/python" ]; then
  echo "Erstelle virtualenv..."
  python3 -m venv "$VENV"
fi

# Abhängigkeiten installieren/aktualisieren
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt

exec "$VENV/bin/python" main.py "$@"
