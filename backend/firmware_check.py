"""
Checks GoodWe's cloud for newer inverter firmware (ARM/DSP), reusing the
exact mechanism the SolarGo app itself uses -- reverse-engineered from
GoodweAPIs.getAllBinFile() / FirmwareInfoAndUpdateActivity.checkNewVersion()
in the SolarGo 7.2.2 APK. Confirmed working 2026-07-15.

No GoodWe account/login needed: the request carries only a static,
non-secret app-identity string (the same fixed value hardcoded in every
SolarGo release), not a real credential. This is a genuine parity feature
with the vendor app, not a bypass of anything access-controlled.

Endpoint: POST https://solargo.sems.com.cn/api/Solargo/CheckUpgradePlus_v2
Payload: [{"sn": <serial>, "type": 1, "firmwares": [
    {"svnVersion": 0, "FirmwareVersion": <current ARM>, "FirmwareType": 2},
    {"svnVersion": 0, "FirmwareVersion": <current DSP>, "FirmwareType": 1}
]}]
Response "data": one entry per component that has a newer version than what
was submitted, with flashVersion (the newer version) and flashFileUrl
(direct .bin download, hosted on GoodWe's own Aliyun OSS bucket).

This module only checks and alerts -- it never downloads or flashes
anything. Applying an update is a manual, deliberate action outside this
app (see README's firmware-update section).
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

import httpx

from config import settings as cfg

log = logging.getLogger(__name__)

_FILE = Path(cfg.db_path).parent / "firmware_check.json"
_URL = "https://solargo.sems.com.cn/api/Solargo/CheckUpgradePlus_v2"

# Static app-identity string the SolarGo app sends with every request to this
# endpoint -- confirmed NOT a secret or session credential (same fixed value
# regardless of login state, hardcoded in AppInfoUtils.getToken()).
_APP_TOKEN = ('{"uid": "","timestamp": 0,"token": "a5b3t89bf7","client": "android",'
              '"version": "","language": "en","projectname":"pvmaster"}')

_FIRMWARE_TYPE_NAMES = {1: "DSP", 2: "ARM"}


@dataclass
class FirmwareCheckState:
    enabled:            bool = True
    last_alerted:       dict = field(default_factory=dict)   # {"1": 26, "2": 22} -- FirmwareType -> version already alerted


def load_state() -> FirmwareCheckState:
    if _FILE.exists():
        try:
            d = json.loads(_FILE.read_text())
            s = FirmwareCheckState()
            for k, v in d.items():
                if hasattr(s, k):
                    setattr(s, k, v)
            return s
        except Exception:
            pass
    return FirmwareCheckState()


def save_state(s: FirmwareCheckState) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(asdict(s), indent=2))


async def check_available_updates(sn: str, arm_version: int, dsp_version: int) -> list[dict]:
    """Query GoodWe's cloud for newer firmware than what's currently running.
    Returns a list of {type_name, current, available, url, released} dicts --
    empty if nothing newer is available or the check fails."""
    payload = [{
        "sn": sn,
        "type": 1,
        "firmwares": [
            {"svnVersion": 0, "FirmwareVersion": arm_version, "FirmwareType": 2},
            {"svnVersion": 0, "FirmwareVersion": dsp_version, "FirmwareType": 1},
        ],
    }]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                _URL, json=payload,
                headers={"token": _APP_TOKEN, "Content-Type": "application/json; charset=utf-8"},
            )
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        log.warning("firmware check failed: %s", e)
        return []

    if body.get("hasError") or str(body.get("code")) != "0":
        log.warning("firmware check returned an error: %s", body.get("msg"))
        return []

    current = {2: arm_version, 1: dsp_version}
    out = []
    for entry in body.get("data") or []:
        ftype = entry.get("flashType")
        name = _FIRMWARE_TYPE_NAMES.get(ftype, f"type {ftype}")
        available = entry.get("flashVersion")
        cur = current.get(ftype)
        if available is None or cur is None or float(available) <= float(cur):
            continue   # not actually newer -- server sometimes echoes back the same/older version
        out.append({
            "type": ftype,
            "type_name": name,
            "current": cur,
            "available": available,
            "url": entry.get("flashFileUrl"),
            "released": entry.get("createTime"),
        })
    return out


async def run_loop(get_inverter, get_notify_config, send_telegram_fn):
    """
    Call from lifespan with:
        get_inverter()        -> goodwe.Inverter | None
        get_notify_config()   -> NotificationConfig (for enabled/bot_token/chat_id)
        send_telegram_fn(nc, text) -> coroutine
    Checks once a day -- firmware releases are infrequent, no reason to poll
    more often, and this hits GoodWe's cloud, not the inverter.
    """
    import asyncio
    await asyncio.sleep(180)   # let the app finish connecting first
    while True:
        try:
            state = load_state()
            inverter = get_inverter()
            if state.enabled and inverter is not None:
                sn = getattr(inverter, "serial_number", None)
                arm = getattr(inverter, "arm_version", None)
                dsp = getattr(inverter, "dsp1_version", None)
                if sn and arm is not None and dsp is not None:
                    updates = await check_available_updates(sn, arm, dsp)
                    changed = False
                    for u in updates:
                        key = str(u["type"])
                        if state.last_alerted.get(key) != u["available"]:
                            nc = get_notify_config()
                            if nc.enabled and nc.bot_token:
                                await send_telegram_fn(nc,
                                    f"🔧 <b>Inverter firmware update available</b>\n"
                                    f"{u['type_name']}: {u['current']} → <b>{u['available']}</b>\n"
                                    f"Released: {u.get('released', '?')}\n"
                                    f"This is a notification only -- apply manually via SolarGo/SEMS "
                                    f"(see README). File: {u['url']}")
                            state.last_alerted[key] = u["available"]
                            changed = True
                            log.info("Firmware update available: %s %s -> %s",
                                     u["type_name"], u["current"], u["available"])
                    if changed:
                        save_state(state)
        except Exception as e:
            log.warning("firmware_check.run_loop: %s", e)
        await asyncio.sleep(86400)   # once/day
