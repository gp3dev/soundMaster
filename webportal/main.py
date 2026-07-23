#!/usr/bin/env python3
"""
SoundMaster Webportal — Auswertung und Verwaltung mehrerer Messstellen.

Verwendung (Docker):
  SM_DB_PATH=/data/webportal.db SM_ADMIN_PASSWORD=... python main.py

Konfiguration ausschließlich über Umgebungsvariablen (siehe config.py).
"""

import secrets

import uvicorn

import auth
import config
import db as database
from api import app, mount_static


if __name__ == "__main__":
    cfg = config.load()
    database.init(cfg.db_path)

    stored_hash = database.auth_get("password_hash")
    if stored_hash is None:
        if cfg.admin_password:
            stored_hash = auth.hash_password(cfg.admin_password)
            database.auth_set("password_hash", stored_hash)
            print("Admin-Passwort aus SM_ADMIN_PASSWORD übernommen.")
        else:
            random_pw = secrets.token_urlsafe(12)
            stored_hash = auth.hash_password(random_pw)
            database.auth_set("password_hash", stored_hash)
            print(f"\n{'=' * 60}")
            print(f"  Admin-Passwort (nur einmalig angezeigt): {random_pw}")
            print(f"{'=' * 60}\n")
    auth.init(stored_hash, session_ttl=cfg.session_ttl_hours * 3600)

    # Registers additional routes onto the shared `app` instance from api.py.
    import ingest  # noqa: F401
    import stations_admin  # noqa: F401

    # Must happen last: the static mount at "/" would otherwise shadow the routes
    # registered above (Starlette matches in registration order).
    mount_static()

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
