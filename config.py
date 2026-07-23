import argparse
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SerialConfig:
    port: str = "/dev/ttyUSB0"
    baud_rate: int = 2400
    timeout: float = 2.0


@dataclass
class DatabaseConfig:
    path: str = "~/.local/share/soundmaster/measurements.db"


@dataclass
class ApiConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class WebportalConfig:
    url: str = ""              # e.g. "https://noise.example.org"; empty = forwarding disabled
    api_key: str = ""
    batch_size: int = 200
    interval: float = 10.0     # seconds between forward attempts when idle/healthy
    timeout: float = 5.0       # HTTP request timeout
    backoff_max: float = 300.0 # cap for exponential backoff on failure
    retain_hours: float = 2.0  # keep already-synced rows at least this long (local live page)


@dataclass
class AppConfig:
    serial: SerialConfig = field(default_factory=SerialConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    webportal: WebportalConfig = field(default_factory=WebportalConfig)
    probe: bool = False
    diag: bool = False
    no_tui: bool = False

    @property
    def db_path(self) -> Path:
        return Path(self.database.path).expanduser()


def load(argv=None) -> AppConfig:
    parser = argparse.ArgumentParser(description="Laserliner SoundTest-Master Logger")
    parser.add_argument("--config", default="config.toml", help="Config file path")
    parser.add_argument("--port", help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, help="Baud rate")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--api-port", type=int, help="REST API port")
    parser.add_argument("--api-host", help="REST API bind address")
    parser.add_argument("--probe", action="store_true", help="Probe serial port and dump raw bytes")
    parser.add_argument("--diag", action="store_true", help="Diagnosemodus: Rohdaten + DB-Schreiben ohne TUI")
    parser.add_argument("--no-tui", action="store_true", help="Run without TUI (API + logging only)")
    parser.add_argument("--webportal-url", help="Webportal-URL für die Datenübertragung (z.B. https://noise.example.org)")
    parser.add_argument("--webportal-key", help="API-Key der Messstelle beim Webportal")
    args = parser.parse_args(argv)

    cfg = AppConfig()

    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        if "serial" in data:
            s = data["serial"]
            cfg.serial.port = s.get("port", cfg.serial.port)
            cfg.serial.baud_rate = s.get("baud_rate", cfg.serial.baud_rate)
            cfg.serial.timeout = s.get("timeout", cfg.serial.timeout)
        if "database" in data:
            cfg.database.path = data["database"].get("path", cfg.database.path)
        if "api" in data:
            a = data["api"]
            cfg.api.host = a.get("host", cfg.api.host)
            cfg.api.port = a.get("port", cfg.api.port)
        if "webportal" in data:
            w = data["webportal"]
            cfg.webportal.url = w.get("url", cfg.webportal.url)
            cfg.webportal.api_key = w.get("api_key", cfg.webportal.api_key)
            cfg.webportal.batch_size = w.get("batch_size", cfg.webportal.batch_size)
            cfg.webportal.interval = w.get("interval", cfg.webportal.interval)
            cfg.webportal.timeout = w.get("timeout", cfg.webportal.timeout)
            cfg.webportal.backoff_max = w.get("backoff_max", cfg.webportal.backoff_max)
            cfg.webportal.retain_hours = w.get("retain_hours", cfg.webportal.retain_hours)

    if args.port:
        cfg.serial.port = args.port
    if args.baud:
        cfg.serial.baud_rate = args.baud
    if args.db:
        cfg.database.path = args.db
    if args.api_port:
        cfg.api.port = args.api_port
    if args.api_host:
        cfg.api.host = args.api_host
    if args.webportal_url:
        cfg.webportal.url = args.webportal_url
    if args.webportal_key:
        cfg.webportal.api_key = args.webportal_key

    cfg.probe = args.probe
    cfg.diag = args.diag
    cfg.no_tui = args.no_tui

    return cfg
