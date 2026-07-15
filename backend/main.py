"""
GoodWe Monitor — FastAPI backend
Inverter comms via goodwe library (Modbus/UDP).
WebSocket live stream, SQLite history, JWT auth.
Costs/tariffs, solar forecast, Telegram notifications.
BeagleBone RS485/CAN BMS bridge via /ws/bms.
"""

import asyncio
import json
import logging
import re
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
from tariffs import load_tariffs, save_tariffs, calc_financials
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
bms_data: dict           = {}
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
      e_total is kWh       → ET stores as Wh, multiply ES by 1000 to match
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
    global latest_data
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
            data.update(bms_data)
            try:
                data["device_tracked_w"] = dev_engine.get_tracked_power()["total_w"]
            except Exception:
                pass
            latest_data = data
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
        await broadcast({"type": "data", "payload": latest_data})
        try:
            db.insert_snapshot(latest_data)
            asyncio.create_task(check_and_notify(latest_data, db.get_today_summary(),
                                                 get_flow=lambda: db.get_energy_flow(None)))
        except Exception as db_err:
            log.warning("DB write skipped: %s\n%s", db_err, traceback.format_exc())

        await asyncio.sleep(cfg.poll_interval)


async def broadcast(msg: dict):
    dead, payload = set(), json.dumps(msg)
    for ws in ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


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
async def _apply_dod_verified(target: int, tries: int = 3) -> bool:
    """Write DoD and confirm it stuck (AA55 writes are occasionally lost).
    Returns True if confirmed OR unverifiable (read timed out → assume applied);
    False only if a successful read shows the wrong value (so we retry next cycle)."""
    from inverter_io import apply_setting, inverter_lock
    last_read = None
    for _ in range(tries):
        await apply_setting(inverter, "dod", target)
        await asyncio.sleep(3)
        try:
            async with inverter_lock:
                d = await inverter.read_settings_data()
            v = d.get("dod"); last_read = int(getattr(v, "value", v))
            if last_read == target:
                return True
        except Exception:
            last_read = None
        await asyncio.sleep(2)
    return last_read is None


async def battery_forecast_scheduler():
    """
    DoD-only battery schedule. IMPORTANT: there is no software charge cap on
    this ES — the goodwe library has no dedicated method for battery charge
    current / max charge SoC (only set_ongrid_battery_dod for the discharge
    floor), and every generic-write attempt at one (eco-mode SoC target,
    charge_i, an eco 0%-power "park") was confirmed silently ignored by the
    inverter (see git history around 2026-07-09/10 and CLAUDE.md). PV surplus
    charges the battery to 100% on a sunny day regardless of anything this
    app writes.

    What this DOES do: hold the battery (DoD 0, no discharge) only while
    producing AND below the release threshold (`max_soc`). Once SoC reaches
    that threshold, the hold is released (DoD -> night_dod) so the battery
    can discharge the moment load exceeds production, instead of being
    locked at 100% with no way down for hours. This can't prevent the peak,
    only stop making the dwell time at that peak worse.
    """
    from battery_schedule import load_schedule
    from forecast import current_hour_kwh
    last_dod = None
    await asyncio.sleep(60)
    while True:
        try:
            sch = load_schedule()
            if sch.enabled and inverter is not None:
                data = await fetch_forecast(load_forecast_config())   # cached (30 min)
                if not data.get("watts"):
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
                    if desired != last_dod:
                        ok = await _apply_dod_verified(desired)
                        last_dod = desired if ok else None   # not confirmed → retry next cycle
                        log.info("Battery forecast schedule: hour=%.2f kWh live=%.2f kW soc=%.0f%% held=%s DoD→%d (%s)",
                                 kwh, live_kw, soc, held, desired, "ok" if ok else "unconfirmed, retrying")
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
    asyncio.create_task(forecast_logger())
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
    if req.password != cfg.password:
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
        result = await apply_setting(inverter, body.key, body.value)
        log.info("Setting written: %s", result)
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/history")
async def get_history(range: str = Query("7d"), _: str = Depends(require_auth)):
    return db.get_history(range)

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

@app.post("/api/battery-schedule")
async def set_battery_schedule(body: dict, _: str = Depends(require_auth)):
    from battery_schedule import load_schedule, save_schedule
    s = load_schedule()
    for k, v in body.items():
        if hasattr(s, k):
            setattr(s, k, v)
    save_schedule(s)
    return {"ok": True}

@app.get("/api/system-config")
async def get_system_config(_: str = Depends(require_auth)):
    return {
        "inverter_host": cfg.inverter_host,
        "poll_interval": cfg.poll_interval,
    }

@app.post("/api/system-config")
async def set_system_config(body: dict, _: str = Depends(require_auth)):
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
        asyncio.create_task(connect_inverter())   # swaps the global `inverter` once connected

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
    t    = load_tariffs()
    rows = db.get_history(range)
    result = []
    totals = {
        "import_cost": 0.0, "export_revenue": 0.0,
        "self_consumed_savings": 0.0, "bat_savings_value": 0.0,
        "net_benefit": 0.0, "co2_avoided_kg": 0.0,
    }
    for row in rows:
        fin = calc_financials(
            t,
            e_imp    = float(row.get("e_day_imp",           0) or 0),
            e_exp    = float(row.get("e_day_exp",           0) or 0),
            e_solar  = float(row.get("e_day",               0) or 0),
            e_load   = float(row.get("e_load_day",          0) or 0),
            e_bat_dis= float(row.get("e_bat_discharge_day", 0) or 0),
        )
        result.append({"date": row.get("ts", ""), **row, **fin})
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
# WebSocket — BeagleBone BMS bridge
# Frame: {"cell_voltages": [...], "temperatures": [...], "soc": 80, ...}
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws/bms")
async def ws_bms(websocket: WebSocket, token: str = Query(...)):
    if not verify_token(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    log.info("BeagleBone BMS bridge connected")
    try:
        while True:
            frame  = json.loads(await websocket.receive_text())
            merged = {f"bms_ext_{k}": v for k, v in frame.items()}
            bms_data.update(merged)
            await broadcast({"type": "data", "payload": {**latest_data, **merged}})
    except WebSocketDisconnect:
        log.info("BeagleBone BMS bridge disconnected")
    except Exception as e:
        log.error("BMS WS error: %s", e)
    finally:
        bms_data.clear()


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
                    r = await auto_engine.execute(act, inverter)
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
    Instantiate one or more automations from a template.
    body: { template_id, params: { key: value, ... } }
    """
    tid    = body.get("template_id", "")
    params = body.get("params", {})

    tpl = next((t for t in auto_engine.TEMPLATES if t["id"] == tid), None)
    if not tpl:
        raise HTTPException(404, f"Template '{tid}' not found")

    autos   = auto_engine.load()
    created = []

    def make(name, description, logic, conditions, actions, cooldown, hysteresis=3.0):
        a = auto_engine.Automation(
            id=auto_engine._next_id(autos + created),
            name=name, description=description,
            logic=logic, conditions=conditions, actions=actions,
            cooldown=cooldown, hysteresis=hysteresis,
        )
        return a

    if tid == "tpl_self_use":
        max_soc      = int(params.get("max_soc", 90))
        min_soc      = int(params.get("min_soc", 20))
        cdwn         = int(params.get("cooldown", 10))
        export_limit = int(params.get("export_limit", 6000))

        created.append(make(
            "Zero export (battery priority)",
            f"Export=0 while SoC<{max_soc}% so solar charges battery first",
            "AND",
            [{"sensor":"battery_soc","op":"lt","value":max_soc,"value2":0}],
            [{"type":"write_setting","setting":"grid_export_limit","value":0,"message":""}],
            cdwn,
        ))
        created.append(make(
            f"Restore export when battery ≥{max_soc}%",
            "Re-enable grid export once battery is full",
            "AND",
            [{"sensor":"battery_soc","op":"gte","value":max_soc,"value2":0}],
            [{"type":"write_setting","setting":"grid_export_limit","value":export_limit,"message":""},
             {"type":"set_general_mode","setting":"","value":None,"message":""}],
            cdwn,
        ))
        created.append(make(
            f"Min SoC floor ≥{min_soc}%",
            f"Start ECO charge when SoC drops below {min_soc}%",
            "AND",
            [{"sensor":"battery_soc","op":"lte","value":min_soc,"value2":0}],
            [{"type":"eco_charge","setting":"","value":None,"soc":min_soc,"message":""},
             {"type":"notify","setting":"","value":None,
              "message":f"🔋 Battery below {min_soc}% — ECO charge to hold {min_soc}%"}],
            cdwn,
        ))
        created.append(make(
            f"Stop ECO charge when full ≥{max_soc}%",
            "Switch back to General mode once battery is charged",
            "AND",
            [{"sensor":"battery_soc","op":"gte","value":max_soc,"value2":0}],
            [{"type":"set_general_mode","setting":"","value":None,"message":""},
             {"type":"notify","setting":"","value":None,
              "message":f"✅ Battery at {max_soc}% — back to General/Self-Use mode"}],
            cdwn,
        ))

    elif tid == "tpl_smart_self_use":
        max_soc      = int(params.get("max_soc", 90))
        min_soc      = int(params.get("min_soc", 20))
        eve_target   = int(params.get("evening_target", 85))
        export_limit = int(params.get("export_limit", 6000))
        hyst         = float(params.get("hysteresis", 3))

        # Rule 1: zero export while battery not full
        # Hysteresis: after firing (SoC was < max_soc), won't re-fire until SoC
        # has risen to max_soc (clearing the lt condition) and then dropped back.
        # The pair with Rule 2 ensures no thrashing at the boundary.
        created.append(make(
            "Zero export → battery first",
            f"No grid export while SoC < {max_soc}%  "
            f"(hysteresis {hyst}% — won't re-fire until SoC rises {hyst}% above last trigger)",
            "AND",
            [{"sensor":"battery_soc","op":"lt","value":max_soc,"value2":0}],
            [{"type":"write_setting","setting":"grid_export_limit","value":0,"message":""}],
            10, hyst,
        ))
        # Rule 2: restore export once full
        created.append(make(
            f"Restore export at {max_soc}%",
            f"Re-enable export when battery ≥ {max_soc}%  "
            f"(hysteresis {hyst}% — won't re-fire until SoC drops to {max_soc - hyst:.0f}% first)",
            "AND",
            [{"sensor":"battery_soc","op":"gte","value":max_soc,"value2":0}],
            [{"type":"write_setting","setting":"grid_export_limit","value":export_limit,"message":""},
             {"type":"set_general_mode","setting":"","value":None,"message":""}],
            10, hyst,
        ))
        # Rule 3: min SoC floor — night reserve protection
        created.append(make(
            f"Min SoC floor {min_soc}% (night reserve)",
            f"Start ECO charge if battery drops ≤ {min_soc}%  "
            f"(hysteresis {hyst}% — re-arms once SoC rises to {min_soc + hyst:.0f}%)",
            "AND",
            [{"sensor":"battery_soc","op":"lte","value":min_soc,"value2":0}],
            [{"type":"eco_charge","setting":"","value":None,"soc":min_soc,"message":""},
             {"type":"notify","setting":"","value":None,
              "message":f"⚠️ SoC below {min_soc}% — ECO charge to hold {min_soc}%"}],
            10, hyst,
        ))
        # Rule 4: pre-evening boost (long cooldown — fires at most once per afternoon)
        created.append(make(
            f"Pre-evening boost → {eve_target}%",
            f"ECO charge if battery below {eve_target}% (cooldown 2 h — fires at most once per afternoon)",
            "AND",
            [{"sensor":"battery_soc","op":"lt","value":eve_target,"value2":0}],
            [{"type":"eco_charge","setting":"","value":None,"soc":eve_target,"message":""},
             {"type":"notify","setting":"","value":None,
              "message":f"☀️ Pre-evening charge started — target {eve_target}% before dark"}],
            120, hyst,
        ))

    else:
        # Generic single automation from template
        a = make(
            params.get("name", tpl.get("name", "Automation")),
            tpl.get("description", ""),
            tpl.get("logic", "AND"),
            tpl.get("conditions", []),
            tpl.get("actions", []),
            tpl.get("cooldown", 5),
        )
        # Apply param overrides to nested structures
        for tp in tpl.get("params", []):
            val = params.get(tp["key"].split(".")[-1], tp.get("default"))
            key = tp["key"]
            if key.startswith("conditions."):
                parts = key.split(".")
                idx   = int(parts[1])
                field = parts[2]
                if idx < len(a.conditions):
                    a.conditions[idx][field] = val
            elif key.startswith("actions."):
                parts = key.split(".")
                idx   = int(parts[1])
                field = parts[2]
                if idx < len(a.actions):
                    a.actions[idx][field] = val
        created.append(a)

    autos.extend(created)
    auto_engine.save(autos)
    return [asdict(c) for c in created]


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
