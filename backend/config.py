"""
Runtime configuration — all values can be overridden via environment variables
or the /data/goodwe-monitor/config.env file that the Proxmox install script creates.
"""

import os
from pathlib import Path

_env_file = next(
    (p for p in [
        Path(__file__).parent.parent / "data" / "config.env",   # project/data/config.env
        Path("/data/goodwe-monitor/config.env"),                  # Linux/Proxmox
    ] if p.exists()),
    None,
)
if _env_file and _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


class Settings:
    inverter_host: str = os.environ.get("INVERTER_HOST", "192.168.1.100")
    poll_interval: int = int(os.environ.get("POLL_INTERVAL", "10"))
    password: str = os.environ.get("APP_PASSWORD", "changeme")
    jwt_secret: str = os.environ.get("JWT_SECRET", "change-this-secret-in-production")
    jwt_expire_days: int = int(os.environ.get("JWT_EXPIRE_DAYS", "30"))
    db_path: str = os.environ.get(
        "DB_PATH",
        str(Path(__file__).parent.parent / "data" / "history.db")
    )


settings = Settings()
