"""
Runtime configuration — values loaded from config.env then environment variables.

On first run, if APP_PASSWORD is still the placeholder value, a secure random
password is auto-generated, written to config.env, and printed to stdout.
"""

import os
import secrets
import string
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ── Locate config file ────────────────────────────────────────────────────────
_env_candidates = [
    Path("/data/goodwe-guru/config.env"),                    # Proxmox LXC (deployed)
    Path(__file__).parent.parent / "data" / "config.env",    # dev: project/data/
]
_env_file = next((p for p in _env_candidates if p.exists()), None)

if _env_file:
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


# ── Auto-generate password on first run ───────────────────────────────────────
_PLACEHOLDER = {"changeme", "test", "change-me", "password", ""}

def _generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def _ensure_password() -> str:
    pw = os.environ.get("APP_PASSWORD", "changeme")
    if pw.lower() in _PLACEHOLDER:
        pw = _generate_password()
        os.environ["APP_PASSWORD"] = pw

        # Persist to config.env so it survives restarts
        env_path = _env_file or _env_candidates[0]
        env_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = env_path.read_text() if env_path.exists() else ""
            if "APP_PASSWORD=" in text:
                lines = [
                    f"APP_PASSWORD={pw}" if l.startswith("APP_PASSWORD=") else l
                    for l in text.splitlines()
                ]
                env_path.write_text("\n".join(lines) + "\n")
            else:
                with env_path.open("a") as f:
                    f.write(f"\nAPP_PASSWORD={pw}\n")
        except Exception as e:
            log.warning("Could not persist generated password: %s", e)

        # Print prominently — user MUST see this on first run
        banner = "=" * 60
        print(f"\n{banner}")
        print("  GoodWe Guru — first run detected")
        print(f"  Auto-generated login password: {pw}")
        print(f"  Saved to: {env_path}")
        print(f"{banner}\n", flush=True)

    return pw


# ── Settings ──────────────────────────────────────────────────────────────────
class Settings:
    inverter_host:  str = os.environ.get("INVERTER_HOST", "192.168.1.100")
    poll_interval:  int = int(os.environ.get("POLL_INTERVAL", "20"))
    password:       str = _ensure_password()
    jwt_secret:     str = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    jwt_expire_days:int = int(os.environ.get("JWT_EXPIRE_DAYS", "30"))
    db_path:        str = os.environ.get(
        "DB_PATH",
        str(Path(__file__).parent.parent / "data" / "history.db"),
    )


settings = Settings()
