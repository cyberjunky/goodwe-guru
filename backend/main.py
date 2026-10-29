"""
GoodWe Monitor — FastAPI backend
Inverter comms via goodwe library (Modbus/UDP).
WebSocket live stream, SQLite history, JWT auth.
Costs/tariffs, solar forecast, Telegram notifications.
"""

import asyncio
import copy
import json
import logging
import re
import secrets
import subprocess
import time
import traceback
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import goodwe
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import create_token, verify_token
from config import settings as cfg
from database import Database
from tariffs import load_tariffs, save_tariffs, calc_financials, day_financials
from forecast import load_forecast_config, save_forecast_config, fetch_forecast, hourly_today, daily_forecast, clear_cache as clear_forecast_cache
from notifications import load_notification_config, save_notification_config, check_and_notify, send_telegram, notify_inverter_connection
import automations as auto_engine
import devices as dev_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared state
# ─────────────────────────────────────────────────────────────────────────────
inverter: goodwe.Inverter | None = None
latest_data: dict        = {}
latest_at: float         = 0.0   # monotonic time of the last successful poll
ws_clients: set[WebSocket] = set()

db: Database   # initialised inside lifespan, not at import time


# ─────────────────────────────────────────────────────────────────────────────
# Inverter polling
# ─────────────────────────────────────────────────────────────────────────────
async def connect_inverter():
    global inverter
    from inverter_io import inverter_lock
    while True:
        try:
            log.info("Connecting to inverter at %s …", cfg.inverter_host)
            async with inverter_lock:
                inverter = await goodwe.connect(cfg.inverter_host)
            log.info("Connected: %s %s", inverter.model_name, inverter.serial_number)
            return
        except Exception as e:
            log.warning("Inverter connection failed: %s – retrying in 10s", e)
            try:
                await notify_inverter_connection(False)
            except Exception:
                pass
            await asyncio.sleep(10)


def normalise(data: dict) -> dict:
    """
    Normalise platform differences so the frontend sees consistent field names.

    ES (platform 105) differences vs ET (platform 745):
      pload / plant_power  → load_ptotal
      pback_up             → backup_ptotal
      e_total is kWh       → passed through unscaled (frontend expects kWh)
      no e_day_imp / e_day_exp / e_load_day on older firmware → set to 0
      house_consumption    → load_ptotal fallback
    """
    d = dict(data)
    # Load power normalisation.
    # Prefer `house_consumption` (true total house load = PV + grid import +
    # battery discharge) over `plant_power` (just the inverter's AC output).
    # Using plant_power made the energy-flow diagram not balance — e.g. grid 814
    # + PV 24 came in but Home showed only ~705.
    if "load_ptotal" not in d:
        d["load_ptotal"] = d.get("house_consumption") or d.get("pload") or d.get("plant_power") or 0
    if "backup_ptotal" not in d:
        d["backup_ptotal"] = d.get("pback_up") or 0

    # Energy totals are presented to the frontend in kWh. ES already reports
    # kWh, so NO scaling — the previous ×1000 made 234.6 kWh show as 234.6 MWh.
    # (ET firmware reports Wh; if ET support is added, divide those by 1000 here.)

    # Grid direction. The frontend convention is: pgrid > 0 = importing,
    # pgrid < 0 = exporting. The ES reports grid power with the opposite sign
    # (negative while importing), which made the dashboard show "Export" during
    # an import and animate the dots the wrong way. Prefer the inverter's own
    # direction flag; fall back to flipping the sign on ES.
    label = str(d.get("grid_in_out_label", "")).lower()
    if d.get("pgrid") is not None:
        p = float(d["pgrid"])
        if "import" in label:
            d["pgrid"] = abs(p)
        elif "export" in label:
            d["pgrid"] = -abs(p)
        elif inverter and inverter.__class__.__name__ == "ES":
            d["pgrid"] = -p

    # Battery power sign. ES reports charging as NEGATIVE; the whole frontend
    # convention is positive = charging (bChg = pbattery1 > 0). Confirmed from
    # logged data: SoC rose 20→99% while pbattery1 stayed negative. Flip it.
    if inverter and inverter.__class__.__name__ == "ES" and d.get("pbattery1") is not None:
        d["pbattery1"] = -float(d["pbattery1"])

    # Missing counters → 0 so the UI shows 0.00 instead of "undefined"
    for key in ("e_day_imp", "e_day_exp", "e_total_imp", "e_total_exp",
                "e_bat_charge_day", "e_bat_discharge_day",
                "e_bat_charge_total", "e_bat_discharge_total"):
        d.setdefault(key, 0)

    return d


async def poll_inverter():
    global latest_data, latest_at
    from inverter_io import inverter_lock
    await connect_inverter()
    await asyncio.sleep(3)
    consecutive_errors = 0
    while True:
        try:
            async with inverter_lock:
                raw = await inverter.read_runtime_data()
            data = {str(k): (v.value if hasattr(v, "value") else v) for k, v in raw.items()}
            data = normalise(data)
            try:
                data["device_tracked_w"] = dev_engine.get_tracked_power()["total_w"]
            except Exception:
                pass
            # ES reports no import/export/load day counters (they read 0) —
            # fill them from the snapshot-derived figures (energy_deriver).
            for k, v in _derived_energy.items():
                if not data.get(k):
                    data[k] = v
            latest_data = data
            latest_at = time.monotonic()
            consecutive_errors = 0
            asyncio.create_task(notify_inverter_connection(True))
        except Exception as e:
            consecutive_errors += 1
            log.warning("Poll error #%d: %s\n%s", consecutive_errors, e, traceback.format_exc())
            asyncio.create_task(notify_inverter_connection(False))
            backoff = min(5 * (2 ** min(consecutive_errors - 1, 3)), 30)
            await asyncio.sleep(backoff)
            if consecutive_errors >= 3:
                log.info("Reconnecting after 3 errors …")
                await connect_inverter()
                await asyncio.sleep(3)
                consecutive_errors = 0
            continue

        # Always broadcast live data — DB write is best-effort
        try:
            await broadcast({"type": "data", "payload": latest_data})
        except Exception as e:
            log.warning("Broadcast failed: %s", e)
        try:
            db.insert_snapshot(latest_data)
            asyncio.create_task(check_and_notify(latest_data, db.get_today_summary(),
                                                 get_flow=lambda: db.get_energy_flow(None)))
        except Exception as db_err:
            log.warning("DB write skipped: %s\n%s", db_err, traceback.format_exc())

        await asyncio.sleep(cfg.poll_interval)


async def broadcast(msg: dict):
    dead, payload = set(), json.dumps(msg)
    # Iterate a snapshot: a tab connecting/closing during an await mutates
    # ws_clients, and "set changed size" would kill the poll loop.
    for ws in list(ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot-derived day energies (import/export/load/battery) — ES lacks the
# counters. Integrating a day of 10 s snapshots takes well under a second, so
# every 5 min is cheap; yesterday is redone too so its last minutes land.
# ─────────────────────────────────────────────────────────────────────────────
_derived_energy: dict = {}


async def energy_deriver():
    global _derived_energy
    from datetime import date, timedelta
    try:
        for d in db.snapshot_dates():          # backfill whatever snapshots remain
            db.update_derived(d)
    except Exception as e:
        log.warning("energy_deriver backfill: %s", e)
    while True:
        try:
            today = date.today()
            db.update_derived((today - timedelta(days=1)).isoformat())
            db.update_derived(today.isoformat())
            _derived_energy = db.energy_totals()
        except Exception as e:
            log.warning("energy_deriver: %s", e)
        await asyncio.sleep(300)


# ─────────────────────────────────────────────────────────────────────────────
# Forecast accuracy logger — records each day's forecast vs actual production
# ─────────────────────────────────────────────────────────────────────────────
async def forecast_logger():
    from datetime import datetime
    await asyncio.sleep(120)   # let the first poll/forecast settle
    while True:
        try:
            fc = load_forecast_config()
            if getattr(fc, "enabled", False):
                today = datetime.now().strftime("%Y-%m-%d")
                data = await fetch_forecast(fc)
                for d in daily_forecast(data):
                    if d["date"] == today:
                        db.record_forecast(today, d["kwh"])   # first-of-day wins
                if latest_data.get("e_day") is not None:
                    db.record_actual(today, float(latest_data["e_day"]))
        except Exception as e:
            log.warning("forecast_logger: %s", e)
        await asyncio.sleep(3600)   # hourly


# ─────────────────────────────────────────────────────────────────────────────
# Forecast-driven battery hold — hold while this hour's solar forecast is above
# a threshold, discharge when below. Re-checks the cached forecast every 5 min.
# ─────────────────────────────────────────────────────────────────────────────
# A discharge while held only counts as a real violation above this power AND
# after this many consecutive 5-min checks. Both guards exist because the
# inverter routinely draws a little from the pack while it hunts around the
# solar-vs-load balance point; that self-corrects within minutes.
_HOLD_DISCHARGE_W = 100
_HOLD_STRIKES     = 3      # 3 × 5 min = sustained for ~15 min
_hold_strikes     = 0

# Watchdogs judge the inverter on polled data; while polls fail, latest_data
# keeps its last value, and counting strikes on that would be judging a ghost.
_STALE_S = 120


def _data_fresh() -> bool:
    return bool(latest_data) and time.monotonic() - latest_at < _STALE_S


# Flash budget for scheduler DoD writes. Steady state is ~2/day; if read-back
# keeps disagreeing, _apply_dod_verified would otherwise write 3× every 5 min
# all day. Past the budget it stops writing until tomorrow and says so once.
_DOD_WRITES_PER_DAY = 12
_dod_budget = {"date": "", "used": 0, "warned": False}


def _dod_budget_take() -> bool:
    from datetime import date
    today = date.today().isoformat()
    if _dod_budget["date"] != today:
        _dod_budget.update(date=today, used=0, warned=False)
    if _dod_budget["used"] >= _DOD_WRITES_PER_DAY:
        return False
    _dod_budget["used"] += 1
    return True


async def _apply_dod_verified(target: int, tries: int = 3, source: str = "battery scheduler") -> bool:
    """Write DoD and confirm it stuck (AA55 writes are occasionally lost).
    Returns True if confirmed OR unverifiable (read timed out → assume applied);
    False only if a successful read shows the wrong value (so we retry next cycle).

    Writes with notify=False and reports once at the end instead, so a retried
    write is still a single Telegram message."""
    from inverter_io import apply_setting, inverter_lock
    from notifications import notify_inverter_write
    last_read = None
    confirmed = False
    for _ in range(tries):
        if not _dod_budget_take():
            if not _dod_budget["warned"]:
                _dod_budget["warned"] = True
                log.warning("DoD write budget (%d/day) used up — not writing until tomorrow",
                            _DOD_WRITES_PER_DAY)
                await notify_inverter_write(
                    "dod", target, "", source,
                    error=f"daily write budget ({_DOD_WRITES_PER_DAY}) used up — the inverter "
                          f"keeps reporting a different DoD; not writing again until tomorrow "
                          f"to spare its flash")
            return False
        await apply_setting(inverter, "dod", target, notify=False)
        await asyncio.sleep(3)
        try:
            async with inverter_lock:
                d = await inverter.read_settings_data()
            v = d.get("dod"); last_read = int(getattr(v, "value", v))
            if last_read == target:
                confirmed = True
                break
        except Exception:
            last_read = None
        await asyncio.sleep(2)

    ok = confirmed or last_read is None
    state = ("confirmed" if confirmed
             else "sent, read-back timed out" if last_read is None
             else f"NOT applied — inverter still reports {last_read}%")
    await notify_inverter_write(
        "dod", target, f"on-grid DoD → {target}% (floor {100 - target}%, {state})",
        source, error="" if ok else state)
    return ok


async def _hold_watchdog(target_dod: int) -> None:
    """Catch the battery discharging while the scheduler believes it is held.

    Most short discharges while held are NOT a fault: when production sits
    just under the house load the inverter's regulation loop hunts around the
    balance point and the pack covers the gap for a while (observed: 589 W
    solar vs a 706 W house → 123 W out of the battery, back to charging a few
    minutes later). Alerting on that would be pure noise, so a violation must
    exceed _HOLD_DISCHARGE_W and persist for _HOLD_STRIKES consecutive 5-min
    checks before it counts.

    Only once it does count is the DoD read back — an extra AA55 read every
    5 min all day is real load on a stack that is already fragile. The read
    then separates two faults that look identical from outside:
      - inverter reports the wrong DoD  → the AA55 write was lost; re-send it
        (worth a flash write, this is the case the retry loop exists for)
      - inverter reports the right DoD  → the firmware is ignoring its own
        floor; re-writing the same value would only burn flash, so alert only.
    """
    global _hold_strikes
    from inverter_io import inverter_lock
    from notifications import notify_hold_violation

    if not _data_fresh():
        return
    pbat = float(latest_data.get("pbattery1") or 0)      # normalised: + = charging
    soc  = float(latest_data.get("battery_soc") or 0)

    if pbat >= -_HOLD_DISCHARGE_W:
        if _hold_strikes >= _HOLD_STRIKES:
            await notify_hold_violation(soc, 0, target_dod, recovered=True)
        _hold_strikes = 0
        return

    _hold_strikes += 1
    if _hold_strikes < _HOLD_STRIKES:
        log.debug("Hold watchdog: discharging %.0f W while held (strike %d/%d) — "
                  "likely balance-point hunting, not acting yet",
                  -pbat, _hold_strikes, _HOLD_STRIKES)
        return
    if _hold_strikes > _HOLD_STRIKES:
        return                                           # already reported, stay quiet

    dod_read = None
    try:
        async with inverter_lock:
            d = await inverter.read_settings_data()
        v = d.get("dod")
        dod_read = int(getattr(v, "value", v))
    except Exception:
        pass                                              # read failed — treat as unknown

    log.warning("Hold watchdog: discharging %.0f W at %.0f%% SoC for %d+ min while held "
                "(target DoD %d, inverter reports %s)",
                -pbat, soc, _HOLD_STRIKES * 5, target_dod,
                dod_read if dod_read is not None else "?")
    await notify_hold_violation(soc, -pbat, dod_read)

    if dod_read is not None and dod_read != target_dod:
        log.info("Hold watchdog: DoD drifted %s → re-sending %d", dod_read, target_dod)
        await _apply_dod_verified(target_dod, source="hold watchdog")


def _save_cap_state(sch) -> None:
    """Persist only the charge cap's own state fields. The tick loaded `sch`
    before awaiting inverter_lock, so saving it whole would overwrite a
    Battery-page edit made in the meantime."""
    from battery_schedule import load_schedule, save_schedule
    cur = load_schedule()
    cur.charge_cap_active    = sch.charge_cap_active
    cur.charge_cap_last_full = sch.charge_cap_last_full
    save_schedule(cur)


async def _charge_cap_tick(sch) -> None:
    """SoC charge cap via the 0x0351 charge-current brake (see inverter_io.py).

    Deliberately gentle on the fragile AA55/WiFi stack: triggering uses only
    the already-polled latest_data, and at most ONE write is attempted per
    5-min cycle (on transitions only — ≤2/day in steady state). A failed
    write leaves the state untouched, so it is simply retried next cycle;
    never retry in a tight loop — the inverter's WiFi module crashes under
    rapid retries.
    """
    from datetime import date
    from inverter_io import apply_setting

    cap = int(getattr(sch, "charge_cap_soc", 0) or 0)
    braked = bool(getattr(sch, "charge_cap_active", False))
    # Amps. Prefer the BMS's own live ceiling so the release always restores
    # full allowed current even if the configured value is stale/too low.
    release_current = int(getattr(sch, "charge_cap_release_current", 50) or 50)
    if not _data_fresh():
        return   # SoC below would be the last value before polls started failing
    bms_limit = int(float(latest_data.get("battery_charge_limit") or 0))
    if bms_limit > 0:
        release_current = max(release_current, bms_limit)
    soc = float(latest_data.get("battery_soc") or 0)
    if soc <= 0:
        return   # no valid SoC yet — don't act blind

    if not (10 <= cap < 100):
        if braked:
            # Cap switched off while braked — release so charging isn't stuck off.
            await apply_setting(inverter, "charge_i", release_current, "charge cap")
            sch.charge_cap_active = False
            _save_cap_state(sch)
            log.info("Charge cap disabled while braked — released (charge_i → %d)", release_current)
        return

    # BMS maintenance: LFP needs a periodic 100% charge for top-balancing /
    # SoC recalibration — skip the cap when one is due, record when done.
    interval = int(getattr(sch, "charge_cap_full_every_days", 0) or 0)
    today = date.today().isoformat()
    try:
        last_full = date.fromisoformat(sch.charge_cap_last_full) if sch.charge_cap_last_full else None
    except ValueError:
        last_full = None
    balance_due = (interval > 0 and last_full != date.today()
                   and (last_full is None or (date.today() - last_full).days >= interval))
    if balance_due:
        if braked:
            await apply_setting(inverter, "charge_i", release_current, "charge cap")
            sch.charge_cap_active = False
            _save_cap_state(sch)
            log.info("Charge cap: BMS-balancing day — released for a full charge")
        if soc >= 99:
            sch.charge_cap_last_full = today
            _save_cap_state(sch)
            log.info("Charge cap: 100%% reached — BMS balance done, cap resumes")
        return

    if not braked and soc >= cap:
        await apply_setting(inverter, "charge_i", 0, "charge cap")
        sch.charge_cap_active = True
        _save_cap_state(sch)
        log.info("Charge cap: SoC %.0f%% ≥ %d%% — charging braked", soc, cap)
    elif braked and soc <= cap - 5:
        await apply_setting(inverter, "charge_i", release_current, "charge cap")
        sch.charge_cap_active = False
        _save_cap_state(sch)
        log.info("Charge cap: SoC %.0f%% ≤ %d%% — charging released", soc, cap - 5)


# Zero export. The CT regulation loop overshoots briefly on every load drop
# (kettle off → a few hundred W out for seconds), so like the hold watchdog
# only a sustained export counts.
_EXPORT_W        = 150
_EXPORT_STRIKES  = 3       # 3 × 5 min
_export_strikes  = 0
_export_verified = False   # one-shot settings check after enable / startup


async def _read_export_settings() -> tuple[int | None, int | None]:
    """(grid_export_limit W, grid_export switch) — (None, None) if unreadable."""
    from inverter_io import inverter_lock
    try:
        async with inverter_lock:
            d = await inverter.read_settings_data()
        val = lambda k: int(getattr(d.get(k), "value", d.get(k)))
        return val("grid_export_limit"), val("grid_export")
    except Exception as e:
        log.info("Zero export: settings read failed (%s)", e)
        return None, None


async def _write_zero_export(limit: int, enabled: int) -> None:
    """Send only what is wrong — each write is a flash write."""
    from inverter_io import apply_setting
    if limit != 0:
        await apply_setting(inverter, "grid_export_limit", 0, "zero export")
    if enabled != 1:
        if limit != 0:
            await asyncio.sleep(3)    # don't fire AA55 commands back-to-back
        await apply_setting(inverter, "grid_export", 1, "zero export")


async def _zero_export_tick(sch) -> None:
    """Keep grid export at 0 W (see battery_schedule.zero_export).

    Gentle on the AA55 stack, same pattern as _hold_watchdog: the settings
    are read once when the feature is enabled (or the app starts), then only
    again when polled grid power shows export above _EXPORT_W for
    _EXPORT_STRIKES consecutive checks. That read tells apart a lost/reset
    setting (re-send it) from the firmware ignoring a correct one (alert
    only — re-writing the same value would just burn flash).
    """
    global _export_strikes, _export_verified
    from notifications import notify_export_violation

    if not getattr(sch, "zero_export", False):
        _export_strikes, _export_verified = 0, False
        return

    if not _export_verified:
        limit, enabled = await _read_export_settings()
        if limit is None:
            return                                   # retry next cycle
        if limit != 0 or enabled != 1:
            log.info("Zero export: limit %d W, switch %d → setting 0 W / on", limit, enabled)
            await _write_zero_export(limit, enabled)
        _export_verified = True
        return

    if not _data_fresh():
        return
    export_w = -float(latest_data.get("pgrid") or 0)    # normalised: < 0 = export
    if export_w < _EXPORT_W:
        if _export_strikes >= _EXPORT_STRIKES:
            await notify_export_violation(0, None, None, recovered=True)
        _export_strikes = 0
        return

    _export_strikes += 1
    if _export_strikes != _EXPORT_STRIKES:
        return                                       # not sustained yet / already reported
    limit, enabled = await _read_export_settings()
    drifted = limit is not None and (limit != 0 or enabled != 1)
    log.warning("Zero export: exporting %.0f W for %d+ min (limit %s W, switch %s)",
                export_w, _EXPORT_STRIKES * 5, limit, enabled)
    await notify_export_violation(export_w, limit, enabled, resending=drifted)
    if drifted:
        await _write_zero_export(limit, enabled)


async def battery_forecast_scheduler():
    """
    Three independent duties, all checked every 5 min:

    1. Forecast hold (sch.enabled): hold the battery (DoD 0, no discharge)
       while producing AND below the release threshold (`max_soc`). Once SoC
       reaches that threshold, the hold is released (DoD -> night_dod) so the
       battery can discharge the moment load exceeds production.

    2. Charge cap (sch.charge_cap_soc): brakes ALL charging once SoC reaches
       the cap (see _charge_cap_tick) — the actual 80% cap the owner wants,
       possible since the 0x0351 command was confirmed working (2026-07-30).

    3. Zero export (sch.zero_export): keep the export limit at 0 W and watch
       for sustained export (see _zero_export_tick).
    """
    global _hold_strikes
    from battery_schedule import load_schedule
    from forecast import current_hour_kwh
    last_dod = None
    await asyncio.sleep(60)
    while True:
        try:
            sch = load_schedule()
            if inverter is not None:
                if sch.enabled:
                    data = await fetch_forecast(load_forecast_config())   # cached (30 min)
                    if not _data_fresh():
                        pass      # polls failing — SoC/PV below would be stale; keep DoD
                    elif not data.get("watts"):
                        # No forecast at all (fetch failed, nothing cached) — don't
                        # guess: keep the current DoD and retry next cycle.
                        log.warning("Battery forecast schedule: no forecast data — keeping DoD as-is")
                    else:
                        kwh = current_hour_kwh(data)
                        # Live PV is a network-independent sanity check: if the
                        # panels are producing ≥ threshold right now, it's daytime
                        # regardless of what the forecast claims.
                        live_kw = float(latest_data.get("ppv") or 0) / 1000.0
                        producing = max(kwh, live_kw) >= float(sch.threshold_kwh)
                        soc = float(latest_data.get("battery_soc") or 0)
                        release_at = int(getattr(sch, "max_soc", 100) or 100)
                        held = producing and (release_at >= 100 or soc < release_at)
                        desired = int(sch.day_dod if held else sch.night_dod)
                        if not held:
                            _hold_strikes = 0     # discharging is expected once released
                        if desired != last_dod:
                            ok = await _apply_dod_verified(desired)
                            last_dod = desired if ok else None   # not confirmed → retry next cycle
                            log.info("Battery forecast schedule: hour=%.2f kWh live=%.2f kW soc=%.0f%% held=%s DoD→%d (%s)",
                                     kwh, live_kw, soc, held, desired, "ok" if ok else "unconfirmed, retrying")
                        elif held:
                            # Steady state while held — verify the inverter is
                            # actually honouring it (read-only unless it isn't).
                            await _hold_watchdog(desired)
                else:
                    last_dod = None
                await _charge_cap_tick(sch)
                await _zero_export_tick(sch)
            else:
                last_dod = None
        except Exception as e:
            log.warning("battery_forecast_scheduler: %s", e)
        await asyncio.sleep(300)   # check every 5 min; writes only on transition


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    global db
    db = Database()
    db.migrate()
    asyncio.create_task(poll_inverter())
    asyncio.create_task(auto_engine.run_loop(
        get_data=lambda: latest_data,
        get_inverter=lambda: inverter,
    ))
    import telegram_bot
    asyncio.create_task(telegram_bot.run_loop(
        get_data=lambda: latest_data,
        get_inverter=lambda: inverter,
        db=db,
    ))
    db.repair_forecast_actuals()
    import firmware_check
    asyncio.create_task(firmware_check.run_loop(
        get_inverter=lambda: inverter,
        get_notify_config=load_notification_config,
        send_telegram_fn=send_telegram,
    ))
    asyncio.create_task(forecast_logger())
    asyncio.create_task(energy_deriver())
    asyncio.create_task(battery_forecast_scheduler())
    asyncio.create_task(dev_engine.poll_devices_loop())
    yield
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="GoodWe Monitor", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

security = HTTPBearer()

def require_auth(creds: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if not verify_token(creds.credentials):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return creds.credentials


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if not secrets.compare_digest(req.password.encode(), cfg.password.encode()):
        await asyncio.sleep(1)    # cheap brake on password guessing
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    return {"access_token": create_token(), "token_type": "bearer"}


# ─────────────────────────────────────────────────────────────────────────────
# Inverter settings
# ─────────────────────────────────────────────────────────────────────────────
class SettingWrite(BaseModel):
    key: str
    value: Any

@app.get("/api/settings")
async def get_settings(_: str = Depends(require_auth)):
    if inverter is None:
        return {}
    try:
        from inverter_io import inverter_lock
        async with inverter_lock:
            raw = await inverter.read_settings_data()
        return {str(k): (v.value if hasattr(v, "value") else v) for k, v in raw.items()}
    except Exception as e:
        log.error("read_settings_data: %s", e)
        return {}

@app.post("/api/settings")
async def write_setting(body: SettingWrite, _: str = Depends(require_auth)):
    if inverter is None:
        raise HTTPException(503, "Inverter not connected")
    try:
        from inverter_io import apply_setting
        result = await apply_setting(inverter, body.key, body.value, "web UI")
        log.info("Setting written: %s", result)
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/history")
async def get_history(range: str = Query("7d"), _: str = Depends(require_auth)):
    return _chronological(db.get_history(range))


def _chronological(rows: list[dict]) -> list[dict]:
    # daily_summary ranges come newest-first; charts read left → right.
    return sorted(rows, key=lambda r: str(r.get("ts", "")))

@app.get("/api/energy-flow")
async def get_energy_flow(date: str | None = Query(None), _: str = Depends(require_auth)):
    return db.get_energy_flow(date)

@app.get("/api/history/day")
async def get_history_day(date: str | None = Query(None), _: str = Depends(require_auth)):
    return db.get_day_series(date)

@app.get("/api/battery-schedule")
async def get_battery_schedule(_: str = Depends(require_auth)):
    from battery_schedule import load_schedule
    from forecast import current_hour_kwh
    s = load_schedule()
    out = asdict(s)
    try:
        data = await fetch_forecast(load_forecast_config())
        kwh = round(current_hour_kwh(data), 3)
        out["hour_forecast_kwh"] = kwh
        out["producing"] = kwh >= float(s.threshold_kwh)
    except Exception as e:
        out["forecast_error"] = str(e)
    return out

_SCHEDULE_BOUNDS = {
    "threshold_kwh": (0, 100), "day_dod": (0, 100), "night_dod": (0, 100),
    "max_soc": (0, 100), "charge_cap_soc": (0, 100),
    "charge_cap_release_current": (1, 500), "charge_cap_full_every_days": (0, 365),
}
_SCHEDULE_STATE = {"charge_cap_active", "charge_cap_last_full"}


@app.post("/api/battery-schedule")
async def set_battery_schedule(body: dict, _: str = Depends(require_auth)):
    global _export_verified
    from battery_schedule import load_schedule, save_schedule
    s = load_schedule()
    if "zero_export" in body:
        _export_verified = False   # re-check the inverter on the next tick
    for k, v in body.items():
        if k in _SCHEDULE_STATE or not hasattr(s, k):
            continue               # scheduler-owned state is not client-writable
        lo_hi = _SCHEDULE_BOUNDS.get(k)
        if lo_hi and not (lo_hi[0] <= float(v) <= lo_hi[1]):
            # An out-of-range DoD is ignored by the inverter, which the
            # scheduler then reads as a lost write and re-sends every cycle.
            raise HTTPException(400, f"{k} must be between {lo_hi[0]} and {lo_hi[1]}")
        setattr(s, k, v)
    save_schedule(s)
    return {"ok": True}

@app.get("/api/firmware-check")
async def get_firmware_check(_: str = Depends(require_auth)):
    import firmware_check
    return asdict(firmware_check.load_state())

@app.post("/api/firmware-check")
async def set_firmware_check(body: dict, _: str = Depends(require_auth)):
    import firmware_check
    s = firmware_check.load_state()
    if "enabled" in body:
        s.enabled = bool(body["enabled"])
    firmware_check.save_state(s)
    return {"ok": True}

@app.post("/api/firmware-check/run")
async def run_firmware_check(_: str = Depends(require_auth)):
    """Manual 'check now' — does not wait for the daily loop."""
    import firmware_check
    if inverter is None:
        raise HTTPException(503, "Inverter not connected")
    sn  = getattr(inverter, "serial_number", None)
    arm = getattr(inverter, "arm_version", None)
    dsp = getattr(inverter, "dsp1_version", None)
    if not sn or arm is None or dsp is None:
        raise HTTPException(503, "Inverter version info not available yet")
    updates = await firmware_check.check_available_updates(sn, arm, dsp)
    return {"current": {"arm": arm, "dsp": dsp}, "updates": updates}

@app.get("/api/system-config")
async def get_system_config(_: str = Depends(require_auth)):
    return {
        "inverter_host": cfg.inverter_host,
        "poll_interval": cfg.poll_interval,
    }

_reconnect_task: asyncio.Task | None = None


@app.post("/api/system-config")
async def set_system_config(body: dict, _: str = Depends(require_auth)):
    global _reconnect_task
    from config import save_env_value
    out = {}

    if "poll_interval" in body:
        seconds = int(body["poll_interval"])
        if not (5 <= seconds <= 300):
            raise HTTPException(400, "poll_interval must be between 5 and 300 seconds")
        cfg.poll_interval = seconds          # takes effect on the poll loop's next sleep — no restart needed
        save_env_value("POLL_INTERVAL", str(seconds))
        out["poll_interval"] = seconds

    if "inverter_host" in body:
        host = str(body["inverter_host"]).strip()
        if not host:
            raise HTTPException(400, "inverter_host cannot be empty")
        cfg.inverter_host = host
        save_env_value("INVERTER_HOST", host)
        out["inverter_host"] = host
        log.info("Inverter host changed to %s — reconnecting …", host)
        # One reconnect at a time: connect_inverter() retries forever, so each
        # host change (or a typo) would otherwise leave another loop running.
        if _reconnect_task and not _reconnect_task.done():
            _reconnect_task.cancel()
        _reconnect_task = asyncio.create_task(connect_inverter())   # swaps the global `inverter` once connected

    return {"ok": True, **out}

@app.get("/api/status")
async def get_status(_: str = Depends(require_auth)):
    return {
        "inverter":     inverter.model_name    if inverter else None,
        "serial":       inverter.serial_number if inverter else None,
        "platform":     inverter.__class__.__name__ if inverter else None,
        "arm_fw":       getattr(inverter, "arm_firmware", None) if inverter else None,
        "firmware":     getattr(inverter, "firmware", None) if inverter else None,
        "arm_version":  getattr(inverter, "arm_version", None) if inverter else None,
        "dsp_version":  getattr(inverter, "dsp1_version", None) if inverter else None,
        "data":         latest_data,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-update (GUI "Update" button)
#
# The systemd service is sandboxed (ReadOnlyPaths=$APP_DIR, NoNewPrivileges) and
# cannot update or restart itself. Instead it drops a trigger file in its
# writable data dir; a privileged `goodwe-guru-update.path` unit notices the
# file and runs update.sh (git pull + rebuild + restart). The trigger content is
# never executed — the action is fixed — so this grants the app no extra rights.
# ─────────────────────────────────────────────────────────────────────────────
APP_DIR        = Path(__file__).resolve().parent.parent
DATA_DIR       = Path(cfg.db_path).resolve().parent
UPDATE_TRIGGER = DATA_DIR / ".update-request"
UPDATE_STATUS  = DATA_DIR / ".update-status.json"


def _git_version() -> dict:
    def g(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(APP_DIR), *args],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            return ""
    return {
        "commit":  g("rev-parse", "--short", "HEAD"),
        "branch":  g("rev-parse", "--abbrev-ref", "HEAD"),
        "date":    g("log", "-1", "--format=%cd", "--date=short"),
        "subject": g("log", "-1", "--format=%s"),
    }


@app.get("/api/version")
async def get_version(_: str = Depends(require_auth)):
    return _git_version()


@app.post("/api/update")
async def trigger_update(_: str = Depends(require_auth)):
    if not DATA_DIR.exists():
        raise HTTPException(500, f"Data dir missing: {DATA_DIR}")
    try:
        UPDATE_STATUS.write_text(json.dumps({"state": "requested", "ts": int(time.time())}))
        UPDATE_TRIGGER.write_text(str(int(time.time())))
    except Exception as e:
        raise HTTPException(500, f"Could not request update: {e}")
    log.info("Update requested via API — trigger written to %s", UPDATE_TRIGGER)
    return {"started": True}


@app.get("/api/update/status")
async def get_update_status(_: str = Depends(require_auth)):
    st: dict = {"state": "idle"}
    if UPDATE_STATUS.exists():
        try:
            st = json.loads(UPDATE_STATUS.read_text())
        except Exception:
            pass
    # Trigger present but updater hasn't picked it up yet
    if UPDATE_TRIGGER.exists() and st.get("state") not in ("running", "requested"):
        st = {"state": "requested"}
    return {"version": _git_version(), "update": st}


_ANSI = re.compile(r"\x1b\[[0-9;]*m")

@app.get("/api/update/log")
async def get_update_log(_: str = Depends(require_auth)):
    log_path = DATA_DIR / "update.log"
    if not log_path.exists():
        return {"log": ""}
    try:
        text = _ANSI.sub("", log_path.read_text(errors="replace"))
        return {"log": text[-8000:]}   # tail, ANSI stripped
    except Exception as e:
        return {"log": f"(could not read update.log: {e})"}


# ─────────────────────────────────────────────────────────────────────────────
# Tariffs / Financial
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/tariffs")
async def get_tariffs(_: str = Depends(require_auth)):
    return asdict(load_tariffs())

@app.post("/api/tariffs")
async def save_tariffs_api(body: dict, _: str = Depends(require_auth)):
    t = load_tariffs()
    for k, v in body.items():
        if hasattr(t, k):
            setattr(t, k, v)
    save_tariffs(t)
    return {"ok": True}

@app.get("/api/financials")
async def get_financials(range: str = Query("7d"), _: str = Depends(require_auth)):
    """
    Returns per-day financial breakdown for the requested range,
    plus totals and cumulative payback progress.
    """
    t = load_tariffs()
    # Priced per DAY even for 12 months — time-of-use needs each day's hourly
    # import split, which a monthly SUM would lose.
    days = {"7d": 7, "30d": 30}.get(range, 366)
    daily = db.daily_rows(days)
    keys = ("e_day", "e_day_exp", "e_day_imp", "e_load_day",
            "e_bat_charge_day", "e_bat_discharge_day")
    buckets: dict[str, dict] = {}
    for row in daily:
        fin = day_financials(t, row)
        label = row["date"][:7] if range == "12m" else row["date"]
        b = buckets.setdefault(label, {"ts": label, **{k: 0.0 for k in keys}, "fin": {}})
        for k in keys:
            b[k] += float(row.get(k) or 0)
        for k, v in fin.items():
            if isinstance(v, (int, float)) and not k.endswith(("_pct", "_rate")):
                b["fin"][k] = b["fin"].get(k, 0.0) + v

    result = []
    totals = {
        "import_cost": 0.0, "export_revenue": 0.0,
        "self_consumed_savings": 0.0, "bat_savings_value": 0.0,
        "net_benefit": 0.0, "co2_avoided_kg": 0.0,
    }
    for label in sorted(buckets):
        b = buckets[label]
        fin = {k: round(v, 3) for k, v in b.pop("fin").items()}
        # Ratios from the bucket's summed energies, not summed percentages.
        ratios = calc_financials(t, e_imp=b["e_day_imp"], e_exp=b["e_day_exp"],
                                 e_solar=b["e_day"], e_load=b["e_load_day"],
                                 e_bat_dis=b["e_bat_discharge_day"], e_bat_chg=b["e_bat_charge_day"])
        fin.update({k: ratios[k] for k in ("self_sufficiency_pct", "self_consumption_pct",
                                           "import_rate", "export_rate", "currency")})
        result.append({"date": label, **{k: round(v, 3) if isinstance(v, float) else v
                                          for k, v in b.items()}, **fin})
        for k in totals:
            totals[k] = round(totals[k] + fin.get(k, 0), 3)

    # Payback progress
    payback = None
    if t.system_cost > 0:
        cumulative = db.get_cumulative_savings(t)
        payback = {
            "system_cost":       t.system_cost,
            "cumulative_savings": round(cumulative, 2),
            "pct_recovered":     round(min(100, cumulative / t.system_cost * 100), 1),
            "remaining":         round(max(0, t.system_cost - cumulative), 2),
        }

    return {"rows": result, "totals": totals, "payback": payback, "currency": t.currency}


# ─────────────────────────────────────────────────────────────────────────────
# Solar Forecast
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/forecast/config")
async def get_forecast_config(_: str = Depends(require_auth)):
    cfg_d = asdict(load_forecast_config())
    try:
        cfg_d["detected_peak_kw"] = round(db.get_peak_pv() / 1000, 2) or None
    except Exception:
        cfg_d["detected_peak_kw"] = None
    return cfg_d

@app.post("/api/forecast/config")
async def save_forecast_config_api(body: dict, _: str = Depends(require_auth)):
    fc = load_forecast_config()
    for k, v in body.items():
        if hasattr(fc, k):
            setattr(fc, k, v)
    save_forecast_config(fc)
    clear_forecast_cache()   # config changed → force a fresh fetch next time
    return {"ok": True}

@app.get("/api/forecast/accuracy")
async def forecast_accuracy(_: str = Depends(require_auth)):
    rows = db.get_forecast_accuracy(14)
    out = []
    for r in rows:
        f, a = r.get("forecast_kwh"), r.get("actual_kwh")
        err = round((a - f) / f * 100, 1) if (f and a is not None) else None
        out.append({**r, "error_pct": err})
    # Overall bias: (actual-forecast)/forecast — >0 means actual exceeded forecast (forecast ran low)
    errs = [o["error_pct"] for o in out if o["error_pct"] is not None]
    bias = round(sum(errs) / len(errs), 1) if errs else None
    return {"days": out, "bias_pct": bias}


@app.get("/api/forecast")
async def get_forecast(force: bool = Query(False), _: str = Depends(require_auth)):
    fc   = load_forecast_config()
    data = await fetch_forecast(fc, force=force)
    return {
        "hourly_today": hourly_today(data),
        "daily":        daily_forecast(data),
        "fetched_at":   data.get("fetched_at"),
        "errors":       data.get("errors", []),
        "source":       data.get("source"),
        "configured":   fc.enabled,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/notifications/config")
async def get_notification_config(_: str = Depends(require_auth)):
    nc = load_notification_config()
    d  = asdict(nc)
    d["bot_token"] = "***" if nc.bot_token else ""   # never return real token
    return d

@app.post("/api/notifications/config")
async def save_notification_config_api(body: dict, _: str = Depends(require_auth)):
    nc = load_notification_config()
    for k, v in body.items():
        if k == "bot_token" and v == "***":
            continue   # don't overwrite with masked value
        if hasattr(nc, k):
            setattr(nc, k, v)
    save_notification_config(nc)
    return {"ok": True}

class TestMessage(BaseModel):
    message: str = "🔔 Test notification from GoodWe Monitor"

@app.post("/api/notifications/test")
async def test_notification(body: TestMessage, _: str = Depends(require_auth)):
    nc = load_notification_config()
    ok = await send_telegram(nc, body.message)
    return {"ok": ok}


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — live inverter data
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws/inverter")
async def ws_inverter(websocket: WebSocket, token: str = Query(...)):
    if not verify_token(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    ws_clients.add(websocket)
    if latest_data:
        await websocket.send_text(json.dumps({"type": "data", "payload": latest_data}))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(websocket)


# ─────────────────────────────────────────────────────────────────────────────
# Automations API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/automations")
async def get_automations(_: str = Depends(require_auth)):
    return [asdict(a) for a in auto_engine.load()]

@app.post("/api/automations")
async def create_automation(body: dict, _: str = Depends(require_auth)):
    autos = auto_engine.load()
    a = auto_engine.Automation()
    a.id = auto_engine._next_id(autos)
    for k, v in body.items():
        if hasattr(a, k) and k != "id":
            setattr(a, k, v)
    autos.append(a)
    auto_engine.save(autos)
    return asdict(a)

@app.put("/api/automations/{aid}")
async def update_automation(aid: str, body: dict, _: str = Depends(require_auth)):
    autos = auto_engine.load()
    for a in autos:
        if a.id == aid:
            for k, v in body.items():
                if hasattr(a, k) and k not in ("id", "last_triggered", "trigger_count"):
                    setattr(a, k, v)
            auto_engine.save(autos)
            return asdict(a)
    raise HTTPException(404, "Not found")

@app.delete("/api/automations/{aid}")
async def delete_automation(aid: str, _: str = Depends(require_auth)):
    autos = auto_engine.load()
    autos = [a for a in autos if a.id != aid]
    auto_engine.save(autos)
    return {"ok": True}

@app.post("/api/automations/{aid}/trigger")
async def trigger_automation(aid: str, _: str = Depends(require_auth)):
    """Manually fire an automation once regardless of conditions."""
    autos = auto_engine.load()
    for a in autos:
        if a.id == aid:
            results = []
            for act in a.actions:
                try:
                    r = await auto_engine.execute(act, inverter, f"automation “{a.name}” (manual)")
                    results.append(r)
                except Exception as e:
                    results.append(f"error: {e}")
            return {"ok": True, "results": results}
    raise HTTPException(404, "Not found")

@app.get("/api/automations/templates")
async def get_templates(_: str = Depends(require_auth)):
    return auto_engine.TEMPLATES

@app.post("/api/automations/from-template")
async def create_from_template(body: dict, _: str = Depends(require_auth)):
    """
    Instantiate an automation from a template.
    body: { template_id, params: { "conditions.0.value": 80, ... } }
    """
    tid    = body.get("template_id", "")
    params = body.get("params", {})

    tpl = next((t for t in auto_engine.TEMPLATES if t["id"] == tid), None)
    if not tpl:
        raise HTTPException(404, f"Template '{tid}' not found")

    autos = auto_engine.load()
    # Deep copies: the param overrides below mutate these dicts, and they
    # must not write back into the shared TEMPLATES.
    a = auto_engine.Automation(
        id=auto_engine._next_id(autos),
        name=params.get("name", tpl.get("name", "Automation")),
        description=tpl.get("description", ""),
        logic=tpl.get("logic", "AND"),
        conditions=copy.deepcopy(tpl.get("conditions", [])),
        actions=copy.deepcopy(tpl.get("actions", [])),
        cooldown=tpl.get("cooldown", 5),
    )
    # Keyed by the full path: two params can share a last segment
    # (peak shaving: conditions.0.value W and conditions.1.value %).
    for tp in tpl.get("params", []):
        section, idx, field = tp["key"].split(".")
        items = a.conditions if section == "conditions" else a.actions
        if int(idx) < len(items):
            items[int(idx)][field] = params.get(tp["key"], tp.get("default"))

    autos.append(a)
    auto_engine.save(autos)
    return [asdict(a)]


# ─────────────────────────────────────────────────────────────────────────────
# Devices — power tracking per device (ping / ARP / always-on)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/devices")
async def get_devices(_: str = Depends(require_auth)):
    devs = dev_engine.load_devices()
    result = []
    for d in devs:
        item = asdict(d)
        on = dev_engine.device_states.get(d.id, d.detection == "always_on")
        item["on"] = on
        item["current_w"] = d.power_on if on else d.power_off
        result.append(item)
    return result

@app.post("/api/devices")
async def create_device(body: dict, _: str = Depends(require_auth)):
    devs = dev_engine.load_devices()
    d = dev_engine.Device()
    d.id = dev_engine.new_id()
    for k, v in body.items():
        if hasattr(d, k) and k != "id":
            setattr(d, k, v)
    devs.append(d)
    dev_engine.save_devices(devs)
    return asdict(d)

@app.put("/api/devices/{did}")
async def update_device(did: str, body: dict, _: str = Depends(require_auth)):
    devs = dev_engine.load_devices()
    for d in devs:
        if d.id == did:
            for k, v in body.items():
                if hasattr(d, k) and k != "id":
                    setattr(d, k, v)
            dev_engine.save_devices(devs)
            return asdict(d)
    raise HTTPException(404, "Not found")

@app.delete("/api/devices/{did}")
async def delete_device(did: str, _: str = Depends(require_auth)):
    devs = dev_engine.load_devices()
    devs = [d for d in devs if d.id != did]
    dev_engine.save_devices(devs)
    dev_engine.device_states.pop(did, None)
    return {"ok": True}

@app.get("/api/devices/library-search")
async def devices_library_search(q: str = Query(""), _: str = Depends(require_auth)):
    return await dev_engine.search_library(q)


# ─────────────────────────────────────────────────────────────────────────────
# Serve built frontend
# ─────────────────────────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        return FileResponse(STATIC_DIR / "index.html")
