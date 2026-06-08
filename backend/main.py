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
from forecast import load_forecast_config, save_forecast_config, fetch_forecast, hourly_today, daily_forecast
from notifications import load_notification_config, save_notification_config, check_and_notify, send_telegram
import automations as auto_engine

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
    while True:
        try:
            log.info("Connecting to inverter at %s …", cfg.inverter_host)
            inverter = await goodwe.connect(cfg.inverter_host)
            log.info("Connected: %s %s", inverter.model_name, inverter.serial_number)
            return
        except Exception as e:
            log.warning("Inverter connection failed: %s – retrying in 10s", e)
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
    # Load power normalisation
    if "load_ptotal" not in d:
        d["load_ptotal"] = d.get("plant_power") or d.get("pload") or d.get("house_consumption") or 0
    if "backup_ptotal" not in d:
        d["backup_ptotal"] = d.get("pback_up") or 0

    # ES e_total is already kWh; ET returns Wh — detect by model
    # (inverter is the global Inverter object — check class name)
    if inverter and inverter.__class__.__name__ == "ES":
        # Convert kWh → Wh to match what the frontend expects for MWh display
        for key in ("e_total", "e_load_total"):
            if key in d and d[key] is not None:
                d[key] = float(d[key]) * 1000

    # Missing counters → 0 so the UI shows 0.00 instead of "undefined"
    for key in ("e_day_imp", "e_day_exp", "e_total_imp", "e_total_exp",
                "e_bat_charge_day", "e_bat_discharge_day",
                "e_bat_charge_total", "e_bat_discharge_total"):
        d.setdefault(key, 0)

    return d


async def poll_inverter():
    global latest_data
    await connect_inverter()
    await asyncio.sleep(3)
    consecutive_errors = 0
    while True:
        try:
            raw  = await inverter.read_runtime_data()
            data = {str(k): (v.value if hasattr(v, "value") else v) for k, v in raw.items()}
            data = normalise(data)
            data.update(bms_data)
            latest_data = data
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log.warning("Poll error #%d: %s\n%s", consecutive_errors, e, traceback.format_exc())
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
            asyncio.create_task(check_and_notify(latest_data, db.get_today_summary()))
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
        await inverter.write_setting(body.key, body.value)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/history")
async def get_history(range: str = Query("7d"), _: str = Depends(require_auth)):
    return db.get_history(range)

@app.get("/api/status")
async def get_status(_: str = Depends(require_auth)):
    return {
        "inverter":  inverter.model_name    if inverter else None,
        "serial":    inverter.serial_number if inverter else None,
        "platform":  inverter.__class__.__name__ if inverter else None,
        "arm_fw":    getattr(inverter, "arm_firmware", None) if inverter else None,
        "data":      latest_data,
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
    return asdict(load_forecast_config())

@app.post("/api/forecast/config")
async def save_forecast_config_api(body: dict, _: str = Depends(require_auth)):
    fc = load_forecast_config()
    for k, v in body.items():
        if hasattr(fc, k):
            setattr(fc, k, v)
    save_forecast_config(fc)
    return {"ok": True}

@app.get("/api/forecast")
async def get_forecast(_: str = Depends(require_auth)):
    fc   = load_forecast_config()
    data = await fetch_forecast(fc)
    return {
        "hourly_today": hourly_today(data),
        "daily":        daily_forecast(data),
        "fetched_at":   data.get("fetched_at"),
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
            [{"type":"eco_charge","setting":"","value":None,"message":""},
             {"type":"notify","setting":"","value":None,
              "message":f"🔋 Battery below {min_soc}% — ECO charge started"}],
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
            [{"type":"eco_charge","setting":"","value":None,"message":""},
             {"type":"notify","setting":"","value":None,
              "message":f"⚠️ SoC below {min_soc}% — ECO charge started"}],
            10, hyst,
        ))
        # Rule 4: pre-evening boost (long cooldown — fires at most once per afternoon)
        created.append(make(
            f"Pre-evening boost → {eve_target}%",
            f"ECO charge if battery below {eve_target}% (cooldown 2 h — fires at most once per afternoon)",
            "AND",
            [{"sensor":"battery_soc","op":"lt","value":eve_target,"value2":0}],
            [{"type":"eco_charge","setting":"","value":None,"message":""},
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
# Serve built frontend
# ─────────────────────────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(_: str):
        return FileResponse(STATIC_DIR / "index.html")
