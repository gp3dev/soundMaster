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
  echo "Bootstrap pip..."
  SITE=$("$VENV/bin/python" -c "import sysconfig; print(sysconfig.get_path('purelib'))")
  python3 -m pip install -q --target "$SITE" pip setuptools wheel
fi

# Abhängigkeiten installieren/aktualisieren
"$VENV/bin/python" -m pip install -q -r requirements.txt

exec "$VENV/bin/python" main.py "$@"
