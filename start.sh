#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

VENV=".venv"

# Virtualenv anlegen falls nicht vorhanden
if [ ! -f "$VENV/bin/python" ]; then
  echo "Erstelle virtualenv..."
  python3 -m venv "$VENV"
fi

# pip bootstrappen falls im venv nicht vorhanden (Debian: ensurepip nicht verfügbar)
if ! "$VENV/bin/python" -m pip --version &>/dev/null 2>&1; then
  echo "Bootstrap pip via get-pip.py..."
  curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  "$VENV/bin/python" /tmp/get-pip.py --quiet
  rm -f /tmp/get-pip.py
fi

# Abhängigkeiten installieren/aktualisieren
"$VENV/bin/python" -m pip install -q -r requirements.txt

exec "$VENV/bin/python" main.py "$@"
