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
from inverter_io import apply_setting

log = logging.getLogger(__name__)
_FILE = Path(cfg.db_path).parent / "automations.json"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

# Sensor, operator and action lists live in the frontend (Automations.tsx);
# _eval_condition / _execute_action below are the source of truth.


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

async def execute(action: dict, inverter: Any, source: str = "automation") -> str:
    """Execute one action. Returns a short description of what was done.

    `source` only labels the Telegram "setting sent" message raised inside
    apply_setting, so an alert names the rule that caused the write."""
    atype = action.get("type")

    if atype == "write_setting":
        key = action.get("setting", "")
        val = action.get("value")
        if key and val is not None and inverter:
            return await apply_setting(inverter, key, val, source)

    elif atype == "set_dod":
        # Battery floor: 0 = no discharge (hold), 80 = normal (floor 20%).
        if inverter:
            return await apply_setting(inverter, "dod", int(action.get("value", 80)), source)

    elif atype == "set_work_mode":
        if inverter:
            return await apply_setting(inverter, "work_mode", int(action.get("value", 0)), source)

    elif atype == "set_general_mode":
        if inverter:
            return await apply_setting(inverter, "work_mode", 0, source)

    elif atype == "eco_charge":
        # ECO_CHARGE convenience mode — ECO + all-day charge schedule capped at the
        # target SoC in one call. Never write eco_mode_1 as a string: EcoModeV2
        # silently rejects it, leaving ECO mode scheduleless and charging to 100%.
        if inverter:
            return await apply_setting(inverter, "eco_charge", int(action.get("soc", 90)), source)

    elif atype == "eco_discharge":
        if inverter:
            return await apply_setting(inverter, "eco_discharge", int(action.get("value") or 100), source)

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
            fired: dict[str, Automation] = {}
            now      = time.time()
            inverter = get_inverter()

            for a in autos:
                if not a.enabled:
                    continue
                if (now - a.last_triggered) < a.cooldown * 60:
                    continue
                if check(a, data):
                    log.info("Automation '%s' triggered (count=%d)", a.name, a.trigger_count + 1)
                    results, failures = [], 0
                    for act in a.actions:
                        try:
                            r = await execute(act, inverter, f"automation “{a.name}”")
                            results.append(r)
                        except Exception as e:
                            log.error("Automation action error: %s", e)
                            results.append(f"error: {e}")
                            failures += 1
                    # Cooldown always applies (no tight retry against the fragile
                    # AA55 stack), but a rule whose every action failed did not
                    # really fire: arming hysteresis would block the retry until
                    # the sensor moved back — e.g. a min-SoC rule never retrying
                    # while SoC keeps falling.
                    a.last_triggered = now
                    if failures < len(a.actions):
                        a.trigger_count += 1
                        # Record sensor values at trigger time for hysteresis tracking
                        a.last_trigger_values = {
                            c["sensor"]: float(data.get(c["sensor"], 0))
                            for c in a.conditions if "sensor" in c
                        }
                    fired[a.id] = a

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

            if fired:
                # Actions await inverter_lock and Telegram (tens of seconds);
                # a rule edited, disabled or deleted meanwhile must not be
                # overwritten — reload and merge only the run-time fields.
                current = load()
                for c in current:
                    f = fired.get(c.id)
                    if f:
                        c.last_triggered      = f.last_triggered
                        c.trigger_count       = f.trigger_count
                        c.last_trigger_values = f.last_trigger_values
                save(current)
        except Exception as e:
            log.error("Automation loop error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in templates
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES = [
    {
        "id":          "tpl_min_soc",
        "name":        "Min SoC Floor",
        "description": "Stop discharging when battery drops below target SoC. "
                       "Protects battery life and ensures a backup reserve. "
                       "Note: the ECO-charge target this uses to recharge is a best-effort "
                       "boost, not a hard stop — it may overshoot past the target SoC.",
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
        "id":          "tpl_night_charge",
        "name":        "Night Charging Window",
        "description": "⚠️ Force-charge the battery (from GRID) during cheap overnight "
                       "tariff hours when SoC is below a threshold. The target SoC is a "
                       "best-effort ECO-mode target, NOT a hard stop — it is confirmed to "
                       "keep charging from the grid past the target on this firmware. "
                       "Pair with Min SoC Floor / monitor manually, or you may rack up "
                       "unexpected grid-import cost overnight.",
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
        "description": "Force-charge (ECO, from the grid if solar is short) whenever the "
                       "battery is below a target SoC, so you get through the night. It has "
                       "NO time-of-day condition — add one or it fires at any hour. Pair with Min SoC Floor to protect the reserve. "
                       "Note: the SoC target is a best-effort ECO-mode boost, not a hard "
                       "stop — it may keep charging (from grid if needed) past the target.",
        "logic":       "AND",
        "conditions":  [
            {"sensor": "battery_soc", "op": "lt", "value": 85, "value2": 0},
        ],
        "actions":     [
            {"type": "eco_charge", "setting": "", "value": None, "soc": 85, "message": ""},
            {"type": "notify", "setting": "", "value": None,
             "message": "🔋 Pre-evening charge boost started — battery below target before evening"},
        ],
        "cooldown":    120,
        "params":      [
            {"key": "conditions.0.value", "label": "Target SoC before evening (%)",
             "type": "number", "min": 50, "max": 100, "default": 85},
            {"key": "actions.0.soc", "label": "Charge to SoC (%)",
             "type": "number", "min": 50, "max": 100, "default": 85},
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
