import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WebportalAppConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    db_path: Path = Path("/data/webportal.db")
    admin_password: Optional[str] = None      # bootstrap only, from SM_ADMIN_PASSWORD
    session_ttl_hours: float = 8.0


def load() -> WebportalAppConfig:
    return WebportalAppConfig(
        host=os.environ.get("SM_HOST", "0.0.0.0"),
        port=int(os.environ.get("SM_PORT", "8080")),
        db_path=Path(os.environ.get("SM_DB_PATH", "/data/webportal.db")),
        admin_password=os.environ.get("SM_ADMIN_PASSWORD") or None,
        session_ttl_hours=float(os.environ.get("SM_SESSION_TTL_HOURS", "8")),
    )
