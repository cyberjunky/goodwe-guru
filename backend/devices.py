"""
Device power tracking — ping/ARP detection + static power values.
Writes to devices.json in the data dir. Polled every 10 s.
PowerCalc community library search: https://api.powercalc.nl/library
"""

import asyncio
import json
import logging
import platform
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from config import settings as cfg

log = logging.getLogger(__name__)
DATA_DIR = Path(cfg.db_path).resolve().parent
DEVICES_FILE = DATA_DIR / "devices.json"


DETECTION_METHODS = ("ping", "arp", "http", "wmi", "always_on", "none")

@dataclass
class Device:
    id: str = ""
    name: str = "Device"
    detection: str = "ping"  # one of DETECTION_METHODS
    ip: str = ""             # used by: ping
    mac: str = ""            # used by: arp
    url: str = ""            # used by: http
    wmi_host: str = ""       # used by: wmi
    power_on: float = 0.0    # W when active
    power_off: float = 0.0   # W standby
    enabled: bool = True
    icon: str = "🔌"


# id → True (on) / False (off)
device_states: dict[str, bool] = {}


def load_devices() -> list[Device]:
    if not DEVICES_FILE.exists():
        return []
    try:
        items = json.loads(DEVICES_FILE.read_text())
        devices = []
        for item in items:
            d = Device(**{k: v for k, v in item.items() if k in Device.__dataclass_fields__})
            # Migrate legacy always_on bool → detection field
            if item.get("always_on") and d.detection == "ping":
                d.detection = "always_on"
            devices.append(d)
        return devices
    except Exception as e:
        log.warning("devices.json load failed: %s", e)
        return []


def save_devices(devices: list[Device]) -> None:
    DEVICES_FILE.write_text(json.dumps([asdict(d) for d in devices], indent=2))


def new_id() -> str:
    return str(uuid.uuid4())[:8]


async def ping_host(ip: str) -> bool:
    try:
        if platform.system() == "Windows":
            args = ["ping", "-n", "1", "-w", "1000", ip]
        else:
            args = ["ping", "-c", "1", "-W", "1", ip]
        proc = await asyncio.create_subprocess_exec(
            *args,
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


async def http_check(url: str) -> bool:
    """True if the URL returns any HTTP response (device has a web interface)."""
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            r = await client.get(url)
            return r.status_code < 600
    except Exception:
        return False


async def wmi_check(host: str) -> bool:
    """
    Windows-only: query remote host via WMI using PowerShell.
    Returns False on non-Windows or if the host is unreachable.
    """
    if platform.system() != "Windows":
        log.debug("wmi_check: skipped on non-Windows for host %s", host)
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NonInteractive", "-Command",
            f"(Get-WmiObject -ComputerName '{host}' -Class Win32_ComputerSystem -ErrorAction Stop).Name",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        return proc.returncode == 0 and bool(stdout.strip())
    except Exception:
        return False


async def check_device(device: Device) -> bool:
    m = device.detection
    if m == "always_on":  return True
    if m == "none":       return False
    if m == "ping":       return await ping_host(device.ip) if device.ip else False
    if m == "arp":        return arp_has_mac(device.mac)    if device.mac else False
    if m == "http":       return await http_check(device.url) if device.url else False
    if m == "wmi":        return await wmi_check(device.wmi_host) if device.wmi_host else False
    return True


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


# ── PowerCalc community library search ────────────────────────────────────────
POWERCALC_API = "https://api.powercalc.nl/library"
_library_cache: list[dict] = []
_library_fetched_at: float = 0.0
_LIBRARY_TTL = 86400.0  # 24 h


async def _ensure_library() -> list[dict]:
    global _library_cache, _library_fetched_at
    if _library_cache and time.monotonic() - _library_fetched_at < _LIBRARY_TTL:
        return _library_cache
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(POWERCALC_API)
            r.raise_for_status()
            data = r.json()
        flat: list[dict] = []
        for mfr in data.get("manufacturers", []):
            mname = mfr.get("full_name", mfr.get("dir_name", ""))
            for model in mfr.get("models", []):
                flat.append({
                    "manufacturer": mname,
                    "name":         model.get("name", ""),
                    "device_type":  model.get("device_type", ""),
                    "standby_power": model.get("standby_power"),
                    "max_power":    model.get("max_power"),
                })
        _library_cache = flat
        _library_fetched_at = time.monotonic()
        log.info("PowerCalc library loaded: %d models", len(flat))
    except Exception as e:
        log.warning("PowerCalc library fetch failed: %s", e)
    return _library_cache


async def search_library(q: str, limit: int = 25) -> list[dict]:
    if not q.strip():
        return []
    terms = q.lower().split()
    results = []
    for entry in await _ensure_library():
        haystack = f"{entry['manufacturer']} {entry['name']}".lower()
        if all(t in haystack for t in terms):
            results.append(entry)
            if len(results) >= limit:
                break
    return results
