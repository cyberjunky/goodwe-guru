"""
Automation engine — evaluate user-defined rules against live inverter data
and execute actions (write settings, change mode, send Telegram) when
conditions are met.

Designed to emulate features the ES inverter lacks natively:
  • Max SoC cap (stop charging above X%)
  • Min SoC floor (stop discharging below Y%)
  • Self-use mode emulation (charge when solar surplus, discharge when load > solar)
  • Time-based tariff windows
  • Peak shaving by SoC
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from config import settings as cfg

log = logging.getLogger(__name__)
_FILE = Path(cfg.db_path).parent / "automations.json"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

SENSORS = {
    "battery_soc":    "Battery SoC",
    "ppv":            "Solar Power (W)",
    "pgrid":          "Grid Power (W)",
    "pbattery":       "Battery Power (W)",
    "load_ptotal":    "Home Load (W)",
    "temperature":    "Inverter Temp (°C)",
    "battery_temperature": "Battery Temp (°C)",
    "fgrid":          "Grid Frequency (Hz)",
}

OPERATORS = {
    "gt":      ">",
    "lt":      "<",
    "gte":     "≥",
    "lte":     "≤",
    "eq":      "=",
    "between": "between",
}

ACTION_TYPES = {
    "set_work_mode":    "Set Work Mode",
    "write_setting":    "Write Setting",
    "eco_charge":       "Start ECO Charge (all day)",
    "eco_discharge":    "Start ECO Discharge (all day)",
    "set_general_mode": "Switch to General Mode",
    "notify":           "Send Telegram Notification",
}


@dataclass
class Condition:
    sensor:  str   = "battery_soc"
    op:      str   = "gte"
    value:   float = 80.0
    value2:  float = 0.0     # upper bound for 'between'


@dataclass
class Action:
    type:    str = "write_setting"
    setting: str = ""          # for write_setting / set_work_mode
    value:   Any = None        # value to write
    message: str = ""          # for notify


@dataclass
class Automation:
    id:            str   = ""
    name:          str   = "New Automation"
    description:   str   = ""
    enabled:       bool  = True
    logic:         str   = "AND"        # AND | OR
    conditions:    list  = field(default_factory=list)
    actions:       list  = field(default_factory=list)
    cooldown:      int   = 5            # minutes between re-triggers
    hysteresis:    float = 3.0          # dead-band: sensor must move this far AWAY
                                        # from trigger before rule can fire again.
                                        # Prevents oscillation at threshold boundary.
                                        # Unit matches the condition sensor (% for SoC, W for power).
    last_triggered: float = 0.0
    last_trigger_values: dict = field(default_factory=dict)  # sensor_key → value at last trigger
    trigger_count: int   = 0


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _next_id(autos: list[Automation]) -> str:
    used = {a.id for a in autos}
    n = 1
    while str(n) in used:
        n += 1
    return str(n)


def load() -> list[Automation]:
    if not _FILE.exists():
        return []
    try:
        raw = json.loads(_FILE.read_text())
        result = []
        for d in raw:
            a = Automation()
            for k, v in d.items():
                if hasattr(a, k):
                    setattr(a, k, v)
            result.append(a)
        return result
    except Exception as e:
        log.error("load automations: %s", e)
        return []


def save(autos: list[Automation]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps([asdict(a) for a in autos], indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Condition evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _eval_condition(c: dict, data: dict) -> bool:
    raw = data.get(c["sensor"])
    if raw is None:
        return False
    val = float(raw)
    v   = float(c.get("value",  0))
    v2  = float(c.get("value2", 0))
    op  = c.get("op", "gt")
    if op == "gt":      return val >  v
    if op == "lt":      return val <  v
    if op == "gte":     return val >= v
    if op == "lte":     return val <= v
    if op == "eq":      return abs(val - v) < 0.5
    if op == "between": return v <= val <= v2
    return False


def _has_recovered(automation: dict, data: dict) -> bool:
    """
    Hysteresis check: after a rule fires, the PRIMARY condition sensor must
    move at least `hysteresis` units away from its trigger value before the
    rule is allowed to fire again.

    Primary sensor = sensor of the first condition in the list.
    If there are no prior trigger values, the rule is free to fire.
    """
    hyst      = float(automation.get("hysteresis", 0))
    ltv       = automation.get("last_trigger_values") or {}
    conditions = automation.get("conditions", [])

    if not ltv or hyst <= 0:
        return True   # no history yet or hysteresis disabled → allow

    for c in conditions:
        sensor  = c.get("sensor", "")
        prev    = ltv.get(sensor)
        if prev is None:
            continue
        current = float(data.get(sensor, 0))
        op      = c.get("op", "gt")
        # Determine which direction is "away" from the trigger
        # For gt/gte conditions: "away" means current < (prev - hyst)
        # For lt/lte conditions: "away" means current > (prev + hyst)
        if op in ("gt", "gte", "between"):
            if current >= (prev - hyst):
                return False   # hasn't recovered downward enough
        elif op in ("lt", "lte"):
            if current <= (prev + hyst):
                return False   # hasn't recovered upward enough

    return True


def check(automation: Automation | dict, data: dict) -> bool:
    if isinstance(automation, Automation):
        automation = asdict(automation)
    if not automation.get("enabled", True):
        return False
    conditions = automation.get("conditions", [])
    if not conditions:
        return False
    # Hysteresis: must have recovered from last trigger before firing again
    if not _has_recovered(automation, data):
        return False
    results = [_eval_condition(c, data) for c in conditions]
    logic   = automation.get("logic", "AND")
    return all(results) if logic == "AND" else any(results)


# ─────────────────────────────────────────────────────────────────────────────
# Action execution
# ─────────────────────────────────────────────────────────────────────────────

async def execute(action: dict, inverter: Any) -> str:
    """Execute one action. Returns a short description of what was done."""
    atype = action.get("type")

    if atype == "write_setting":
        key = action.get("setting", "")
        val = action.get("value")
        if key and val is not None and inverter:
            await inverter.write_setting(key, val)
            return f"write_setting({key}={val})"

    elif atype == "set_work_mode":
        mode = int(action.get("value", 0))
        if inverter:
            await inverter.write_setting("work_mode", mode)
            labels = {0:"General",1:"Off-Grid",2:"Backup",3:"Eco",4:"Peak Shaving",5:"Self-Use"}
            return f"work_mode → {labels.get(mode, mode)}"

    elif atype == "set_general_mode":
        if inverter:
            await inverter.write_setting("work_mode", 0)
            return "work_mode → General (0)"

    elif atype == "eco_charge":
        # Switch to Eco mode with an all-day charge schedule, capped at a target
        # SoC (NOT 100%). The SoC cap field prevents the battery being pinned full.
        if inverter:
            soc = int(action.get("soc", 90))
            soc = max(10, min(soc, 100))
            await inverter.write_setting("work_mode", 3)
            try:
                # start-end-power%-SoC cap-charge(1)
                await inverter.write_setting("eco_mode_1", f"00:00-23:59-100-{soc}-1")
            except Exception:
                pass
            return f"ECO charge to {soc}%"

    elif atype == "eco_discharge":
        if inverter:
            await inverter.write_setting("work_mode", 3)
            try:
                await inverter.write_setting("eco_mode_1", "00:00-23:59-100-0-0")
            except Exception:
                pass
            return "ECO discharge mode (all day)"

    elif atype == "notify":
        msg = action.get("message", "Automation triggered")
        try:
            from notifications import load_notification_config, send_telegram
            nc = load_notification_config()
            await send_telegram(nc, f"⚙️ <b>Automation:</b> {msg}")
            return f"Telegram: {msg[:40]}"
        except Exception as e:
            return f"notify failed: {e}"

    return f"unknown action: {atype}"


# ─────────────────────────────────────────────────────────────────────────────
# Background evaluation loop
# ─────────────────────────────────────────────────────────────────────────────

async def run_loop(get_data, get_inverter):
    """
    Call from lifespan with callables:
        get_data()     → dict of latest inverter data
        get_inverter() → goodwe.Inverter | None
    Evaluates all automations every 30 seconds.
    """
    import asyncio
    while True:
        await asyncio.sleep(30)
        data = get_data()
        if not data:
            continue
        try:
            autos    = load()
            changed  = False
            now      = time.time()
            inverter = get_inverter()

            for a in autos:
                if not a.enabled:
                    continue
                if (now - a.last_triggered) < a.cooldown * 60:
                    continue
                if check(a, data):
                    log.info("Automation '%s' triggered (count=%d)", a.name, a.trigger_count + 1)
                    results = []
                    for act in a.actions:
                        try:
                            r = await execute(act, inverter)
                            results.append(r)
                        except Exception as e:
                            log.error("Automation action error: %s", e)
                            results.append(f"error: {e}")
                    a.last_triggered  = now
                    a.trigger_count  += 1
                    # Record sensor values at trigger time for hysteresis tracking
                    a.last_trigger_values = {
                        c["sensor"]: float(data.get(c["sensor"], 0))
                        for c in a.conditions if "sensor" in c
                    }
                    changed = True

                    # Telegram summary of trigger
                    try:
                        from notifications import load_notification_config, send_telegram
                        nc = load_notification_config()
                        if nc.enabled and nc.bot_token:
                            await send_telegram(nc,
                                f"⚙️ <b>Automation triggered:</b> {a.name}\n"
                                f"Actions: {', '.join(results)}")
                    except Exception:
                        pass

            if changed:
                save(autos)
        except Exception as e:
            log.error("Automation loop error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in templates
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES = [
    {
        "id":          "tpl_max_soc",
        "name":        "Max SoC Limit",
        "description": "Stop charging when battery reaches the target SoC. "
                       "Emulates the upper SoC cap in Self-Use mode.",
        "logic":       "AND",
        "conditions":  [{"sensor": "battery_soc", "op": "gte", "value": 80, "value2": 0}],
        "actions":     [
            {"type": "write_setting", "setting": "work_mode", "value": 0,
             "message": ""},
        ],
        "cooldown":    10,
        "params":      [
            {"key": "conditions.0.value", "label": "Max SoC (%)", "type": "number",
             "min": 50, "max": 99, "default": 80},
        ],
    },
    {
        "id":          "tpl_min_soc",
        "name":        "Min SoC Floor",
        "description": "Stop discharging when battery drops below target SoC. "
                       "Protects battery life and ensures a backup reserve.",
        "logic":       "AND",
        "conditions":  [{"sensor": "battery_soc", "op": "lte", "value": 20, "value2": 0}],
        "actions":     [
            {"type": "eco_charge", "setting": "", "value": None, "soc": 20, "message": ""},
        ],
        "cooldown":    10,
        "params":      [
            {"key": "conditions.0.value", "label": "Min SoC (%)", "type": "number",
             "min": 5, "max": 50, "default": 20},
            {"key": "actions.0.soc", "label": "Hold SoC at (%)", "type": "number",
             "min": 5, "max": 60, "default": 20},
        ],
    },
    {
        "id":          "tpl_self_use",
        "name":        "Self-Use Emulation (ES)",
        "description": "Creates two automations that together replicate Self-Use mode: "
                       "max SoC cap (default 90%) and min SoC floor (default 15%). "
                       "Between these limits the inverter operates in General mode "
                       "which already prioritises solar self-consumption on the ES.",
        "multi":       True,   # creates multiple automations
        "params":      [
            {"key": "max_soc", "label": "Max SoC (%)",  "type": "number", "min": 60, "max": 99, "default": 90},
            {"key": "min_soc", "label": "Min SoC (%)",  "type": "number", "min": 5,  "max": 40, "default": 15},
            {"key": "cooldown","label": "Cooldown (min)","type": "number", "min": 1,  "max": 60, "default": 10},
        ],
    },
    {
        "id":          "tpl_night_charge",
        "name":        "Night Charging Window",
        "description": "Force-charge the battery during cheap overnight tariff hours "
                       "when SoC is below a threshold.",
        "logic":       "AND",
        "conditions":  [
            {"sensor": "battery_soc", "op": "lt", "value": 80, "value2": 0},
        ],
        "actions":     [
            {"type": "eco_charge", "setting": "", "value": None, "soc": 80, "message": ""},
        ],
        "cooldown":    60,
        "params":      [
            {"key": "conditions.0.value", "label": "Charge until SoC (%)", "type": "number",
             "min": 50, "max": 100, "default": 80},
            {"key": "actions.0.soc", "label": "Charge target SoC (%)", "type": "number",
             "min": 50, "max": 100, "default": 80},
        ],
    },
    {
        "id":          "tpl_peak_shaving",
        "name":        "Peak Shaving Guard",
        "description": "Start discharging when grid import exceeds a threshold, "
                       "but only if battery SoC is sufficient.",
        "logic":       "AND",
        "conditions":  [
            {"sensor": "pgrid",       "op": "gt",  "value": 2000, "value2": 0},
            {"sensor": "battery_soc", "op": "gt",  "value": 30,   "value2": 0},
        ],
        "actions":     [
            {"type": "eco_discharge", "setting": "", "value": None, "message": ""},
        ],
        "cooldown":    5,
        "params":      [
            {"key": "conditions.0.value", "label": "Import threshold (W)", "type": "number",
             "min": 500, "max": 10000, "default": 2000},
            {"key": "conditions.1.value", "label": "Min SoC to discharge (%)", "type": "number",
             "min": 10,  "max": 80,    "default": 30},
        ],
    },
    {
        "id":          "tpl_pre_evening",
        "name":        "Pre-Evening Charge Boost",
        "description": "If the battery is below a target SoC before a set hour "
                       "(e.g. 16:00), force-charge it so you can get through the night "
                       "without grid imports. Pair with Min SoC Floor to protect the reserve.",
        "logic":       "AND",
        "conditions":  [
            {"sensor": "battery_soc", "op": "lt", "value": 85, "value2": 0},
        ],
        "actions":     [
            {"type": "eco_charge", "setting": "", "value": None, "message": ""},
            {"type": "notify", "setting": "", "value": None,
             "message": "🔋 Pre-evening charge boost started — battery below target before evening"},
        ],
        "cooldown":    120,
        "params":      [
            {"key": "conditions.0.value", "label": "Target SoC before evening (%)",
             "type": "number", "min": 50, "max": 100, "default": 85},
        ],
    },
    {
        "id":          "tpl_zero_export",
        "name":        "Zero Export While Charging",
        "description": "Set grid export limit to 0 W while the battery is not yet full, "
                       "so solar goes into the battery first. Restore normal export limit "
                       "once battery exceeds target SoC.",
        "logic":       "AND",
        "conditions":  [{"sensor": "battery_soc", "op": "lt", "value": 90, "value2": 0}],
        "actions":     [
            {"type": "write_setting", "setting": "grid_export_limit", "value": 0, "message": ""},
        ],
        "cooldown":    10,
        "params":      [
            {"key": "conditions.0.value", "label": "Keep export=0 until SoC (%)",
             "type": "number", "min": 50, "max": 100, "default": 90},
        ],
    },
    {
        "id":          "tpl_restore_export",
        "name":        "Restore Export When Battery Full",
        "description": "Companion to Zero Export — re-enables grid export once the battery "
                       "reaches the target SoC. Pair both rules together.",
        "logic":       "AND",
        "conditions":  [{"sensor": "battery_soc", "op": "gte", "value": 90, "value2": 0}],
        "actions":     [
            {"type": "write_setting", "setting": "grid_export_limit", "value": 6000, "message": ""},
            {"type": "set_general_mode", "setting": "", "value": None, "message": ""},
        ],
        "cooldown":    10,
        "params":      [
            {"key": "conditions.0.value",   "label": "Re-enable export at SoC (%)",
             "type": "number", "min": 60, "max": 100, "default": 90},
            {"key": "actions.0.value",      "label": "Export limit to restore (W)",
             "type": "number", "min": 0, "max": 15000, "default": 6000},
        ],
    },
    {
        "id":          "tpl_smart_self_use",
        "name":        "Smart Self-Use (complete set)",
        "description": "Creates 4 automations together:\n"
                       "1. Zero export → battery first (battery < max SoC)\n"
                       "2. Restore export when battery full\n"
                       "3. Pre-evening boost (charge if SoC < target before 15:00)\n"
                       "4. Min SoC floor to protect battery overnight\n"
                       "This is the closest to Self-Use mode on the ES.",
        "multi":       True,
        "params":      [
            {"key": "max_soc",   "label": "Max SoC / full battery (%)", "type": "number",
             "min": 70, "max": 100, "default": 90},
            {"key": "min_soc",   "label": "Min SoC / night reserve (%)", "type": "number",
             "min": 5,  "max":  40, "default": 20},
            {"key": "evening_target", "label": "Evening SoC target (%)", "type": "number",
             "min": 60, "max": 100, "default": 85},
            {"key": "export_limit", "label": "Normal export limit (W)", "type": "number",
             "min": 0,  "max": 15000, "default": 6000},
            {"key": "hysteresis", "label": "Hysteresis band (%)", "type": "number",
             "min": 1,  "max": 15,   "default": 3,
             "hint": "SoC must move this far from trigger before rule can re-fire. Prevents oscillation."},
        ],
    },
    {
        "id":          "tpl_high_temp",
        "name":        "High Temperature Alert",
        "description": "Send a Telegram alert when the inverter temperature exceeds a threshold.",
        "logic":       "AND",
        "conditions":  [{"sensor": "temperature", "op": "gt", "value": 65, "value2": 0}],
        "actions":     [
            {"type": "notify", "setting": "", "value": None,
             "message": "⚠️ Inverter temperature is high!"},
        ],
        "cooldown":    30,
        "params":      [
            {"key": "conditions.0.value", "label": "Temp threshold (°C)", "type": "number",
             "min": 50, "max": 90, "default": 65},
        ],
    },
]
