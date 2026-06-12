"""
Device power tracking — ping/ARP detection + static power values.
Writes to devices.json in the data dir. Polled every 10 s.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from config import settings as cfg

log = logging.getLogger(__name__)
DATA_DIR = Path(cfg.db_path).resolve().parent
DEVICES_FILE = DATA_DIR / "devices.json"


@dataclass
class Device:
    id: str = ""
    name: str = "Device"
    ip: str = ""           # ping this IP to detect on/off
    mac: str = ""          # fallback: check ARP table for MAC
    always_on: bool = False
    power_on: float = 0.0  # W when active
    power_off: float = 0.0 # W standby
    enabled: bool = True
    icon: str = "🔌"


# id → True (on) / False (off)
device_states: dict[str, bool] = {}


def load_devices() -> list[Device]:
    if not DEVICES_FILE.exists():
        return []
    try:
        items = json.loads(DEVICES_FILE.read_text())
        return [Device(**{k: v for k, v in item.items() if k in Device.__dataclass_fields__})
                for item in items]
    except Exception as e:
        log.warning("devices.json load failed: %s", e)
        return []


def save_devices(devices: list[Device]) -> None:
    DEVICES_FILE.write_text(json.dumps([asdict(d) for d in devices], indent=2))


def new_id() -> str:
    return str(uuid.uuid4())[:8]


async def ping_host(ip: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=3.0)
        return proc.returncode == 0
    except Exception:
        return False


def arp_has_mac(mac: str) -> bool:
    norm = mac.lower().replace("-", ":").strip()
    try:
        return norm in Path("/proc/net/arp").read_text().lower()
    except Exception:
        return False


async def check_device(device: Device) -> bool:
    if device.always_on:
        return True
    if device.ip:
        return await ping_host(device.ip)
    if device.mac:
        return arp_has_mac(device.mac)
    return True  # no detection method → assume on


async def poll_devices_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            devs = load_devices()
            enabled = [d for d in devs if d.enabled]
            results = await asyncio.gather(*(check_device(d) for d in enabled), return_exceptions=True)
            for d, res in zip(enabled, results):
                device_states[d.id] = bool(res) if not isinstance(res, Exception) else device_states.get(d.id, False)
            valid = {d.id for d in devs}
            for stale in list(device_states):
                if stale not in valid:
                    del device_states[stale]
        except Exception as e:
            log.warning("poll_devices_loop: %s", e)
        await asyncio.sleep(10)


def get_tracked_power() -> dict:
    devs = load_devices()
    items = []
    total = 0.0
    for d in devs:
        if not d.enabled:
            continue
        on = device_states.get(d.id, d.always_on)
        w = d.power_on if on else d.power_off
        total += w
        items.append({"id": d.id, "name": d.name, "icon": d.icon, "on": on, "power_w": w})
    return {"total_w": round(total), "devices": items}
