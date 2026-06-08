"""
Telegram notification engine.

Events detected on every inverter poll tick:
  - battery_critical   SoC ≤ bat_critical_soc
  - battery_low        SoC ≤ bat_low_soc (and > critical)
  - battery_recovered  SoC > bat_low_soc + hysteresis after being low
  - battery_full       SoC = 100 % (first time in session)
  - fault_detected     error_codes ≠ 0
  - fault_cleared      error_codes = 0 after fault
  - grid_outage        work_mode contains 'Off-Grid' or 'Backup'
  - grid_restored      grid back after outage
  - solar_started      ppv crosses 50 W upward in morning
  - solar_stopped      ppv drops below 50 W in evening
  - high_import        pgrid > high_import_threshold_w
  - daily_summary      sent once at configured wall-clock hour

Setup:
  1. Create a Telegram bot via @BotFather → get BOT_TOKEN
  2. Start a chat with the bot or add it to a group → get CHAT_ID
     (send /start then https://api.telegram.org/bot{TOKEN}/getUpdates)
  3. Enter BOT_TOKEN + CHAT_ID in Settings → Notifications
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Any

import httpx

from config import settings as cfg

log = logging.getLogger(__name__)

_CONFIG_FILE = Path(cfg.db_path).parent / "notifications.json"


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class NotificationConfig:
    enabled:                  bool  = False
    bot_token:                str   = ""
    chat_id:                  str   = ""

    # Event switches
    bat_critical_enabled:     bool  = True
    bat_low_enabled:          bool  = True
    bat_full_enabled:         bool  = True
    fault_enabled:            bool  = True
    grid_outage_enabled:      bool  = True
    solar_start_stop_enabled: bool  = False
    high_import_enabled:      bool  = False
    daily_summary_enabled:    bool  = True

    # Thresholds
    bat_critical_soc:         int   = 10
    bat_low_soc:              int   = 20
    bat_hysteresis:           int   = 5   # must recover this many % above low threshold
    high_import_threshold_w:  int   = 3000
    daily_summary_hour:       int   = 20  # wall-clock hour (0-23) for daily summary


def load_notification_config() -> NotificationConfig:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text())
            nc = NotificationConfig()
            for k, v in data.items():
                if hasattr(nc, k):
                    setattr(nc, k, v)
            return nc
        except Exception:
            pass
    return NotificationConfig()


def save_notification_config(nc: NotificationConfig):
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(asdict(nc), indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Telegram sender
# ─────────────────────────────────────────────────────────────────────────────
async def send_telegram(nc: NotificationConfig, text: str) -> bool:
    if not nc.bot_token or not nc.chat_id:
        log.warning("Telegram not configured — skipping notification")
        return False
    url = f"https://api.telegram.org/bot{nc.bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "chat_id":    nc.chat_id,
                "text":       text,
                "parse_mode": "HTML",
            })
            r.raise_for_status()
            return True
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def fmt_power(w: float) -> str:
    """Power as W below 1 kW, kW above — matches the dashboard formatting."""
    a = abs(float(w or 0))
    return f"{a / 1000:.2f} kW" if a >= 1000 else f"{round(a)} W"


# ─────────────────────────────────────────────────────────────────────────────
# Event state machine
# ─────────────────────────────────────────────────────────────────────────────
class NotificationState:
    """Tracks edge states to avoid duplicate notifications."""

    def __init__(self):
        self.bat_critical_sent     = False
        self.bat_low_sent          = False
        self.bat_full_sent         = False
        self.fault_active          = False
        self.last_fault_codes      = ""
        self.grid_outage_active    = False
        self.solar_active          = False
        self.high_import_sent_at   = 0.0
        self.last_daily_summary_date: date | None = None


_state = NotificationState()


async def check_and_notify(data: dict[str, Any], daily_stats: dict[str, Any] | None = None):
    """
    Call once per poll tick with the latest inverter data.
    daily_stats: today's summary from the database (for daily summary message).
    """
    nc = load_notification_config()
    if not nc.enabled or not nc.bot_token or not nc.chat_id:
        return

    soc         = int(data.get("battery_soc", 100))
    ppv         = float(data.get("ppv", 0))
    pgrid       = float(data.get("pgrid", 0))
    error_codes = str(data.get("error_codes", "0") or "0")
    work_mode   = str(data.get("work_mode_label", ""))
    now         = datetime.now()

    # ── Battery critical ──────────────────────────────────────────────────
    if nc.bat_critical_enabled and soc <= nc.bat_critical_soc:
        if not _state.bat_critical_sent:
            await send_telegram(nc,
                f"🔴 <b>Battery Critical!</b>\n"
                f"State of Charge: <b>{soc}%</b>\n"
                f"⚠️ Charge immediately to avoid damage or outage.")
            _state.bat_critical_sent = True
    elif soc > nc.bat_critical_soc + _state.__class__.__dict__.get("bat_hysteresis", nc.bat_hysteresis):
        _state.bat_critical_sent = False

    # ── Battery low ───────────────────────────────────────────────────────
    if nc.bat_low_enabled and nc.bat_critical_soc < soc <= nc.bat_low_soc:
        if not _state.bat_low_sent:
            await send_telegram(nc,
                f"🟡 <b>Battery Low</b>\n"
                f"State of Charge: <b>{soc}%</b>")
            _state.bat_low_sent = True
    elif soc > nc.bat_low_soc + nc.bat_hysteresis:
        _state.bat_low_sent = False

    # ── Battery full ──────────────────────────────────────────────────────
    if nc.bat_full_enabled and soc >= 100:
        if not _state.bat_full_sent:
            await send_telegram(nc, "✅ <b>Battery fully charged</b> (100% SoC)")
            _state.bat_full_sent = True
    elif soc < 98:
        _state.bat_full_sent = False

    # ── Fault codes ───────────────────────────────────────────────────────
    if nc.fault_enabled:
        has_fault = error_codes not in ("0", "0x0", "0x00000000", "", "None")
        if has_fault and not _state.fault_active:
            _state.fault_active    = True
            _state.last_fault_codes = error_codes
            await send_telegram(nc,
                f"⚠️ <b>Inverter Fault Detected</b>\n"
                f"Error code: <code>{error_codes}</code>\n"
                f"Work mode: {work_mode}\n"
                f"Check the Faults page for details.")
        elif not has_fault and _state.fault_active:
            _state.fault_active = False
            await send_telegram(nc, "✅ <b>Inverter fault cleared</b> — system back to normal.")

    # ── Grid outage ───────────────────────────────────────────────────────
    if nc.grid_outage_enabled:
        on_backup = "off-grid" in work_mode.lower() or "backup" in work_mode.lower()
        if on_backup and not _state.grid_outage_active:
            _state.grid_outage_active = True
            await send_telegram(nc,
                f"🔌 <b>Grid Outage!</b>\n"
                f"Inverter switched to: <b>{work_mode}</b>\n"
                f"Running on battery (SoC: {soc}%)")
        elif not on_backup and _state.grid_outage_active:
            _state.grid_outage_active = False
            await send_telegram(nc, "🔋 <b>Grid Restored</b> — back to normal operation.")

    # ── Solar started / stopped ───────────────────────────────────────────
    if nc.solar_start_stop_enabled:
        solar_on = ppv > 50
        if solar_on and not _state.solar_active:
            _state.solar_active = True
            await send_telegram(nc,
                f"☀️ <b>Solar production started</b>\n"
                f"Current output: <b>{fmt_power(ppv)}</b>")
        elif not solar_on and _state.solar_active and now.hour >= 14:
            # Only send "stopped" in afternoon/evening
            _state.solar_active = False
            e_day = float(data.get("e_day", 0))
            await send_telegram(nc,
                f"🌙 <b>Solar production ended for today</b>\n"
                f"Total yield: <b>{e_day:.2f} kWh</b>")

    # ── High grid import ──────────────────────────────────────────────────
    if nc.high_import_enabled and pgrid > nc.high_import_threshold_w:
        if time.time() - _state.high_import_sent_at > 3600:  # max once/hour
            _state.high_import_sent_at = time.time()
            await send_telegram(nc,
                f"📈 <b>High Grid Import Alert</b>\n"
                f"Currently importing <b>{fmt_power(pgrid)}</b> from the grid\n"
                f"(threshold: {fmt_power(nc.high_import_threshold_w)})")

    # ── Daily summary ─────────────────────────────────────────────────────
    if nc.daily_summary_enabled and daily_stats:
        today = now.date()
        if (now.hour == nc.daily_summary_hour
                and _state.last_daily_summary_date != today):
            _state.last_daily_summary_date = today
            e_day     = float(daily_stats.get("e_day",     0))
            e_exp     = float(daily_stats.get("e_day_exp", 0))
            e_imp     = float(daily_stats.get("e_day_imp", 0))
            e_load    = float(daily_stats.get("e_load_day",0))
            e_bch     = float(daily_stats.get("e_bat_charge_day",   0))
            e_bdis    = float(daily_stats.get("e_bat_discharge_day",0))

            # Financial snapshot if tariffs configured
            try:
                from tariffs import load_tariffs, calc_financials
                t = load_tariffs()
                fin = calc_financials(t, e_imp, e_exp, e_day, e_load, e_bdis)
                fin_line = (
                    f"\n💶 Net benefit today: <b>{fin['currency']}{fin['net_benefit']:.2f}</b>"
                    f"\n   Import cost:  {fin['currency']}{fin['import_cost']:.2f}"
                    f"\n   Solar savings: {fin['currency']}{fin['self_consumed_savings']:.2f}"
                    f"\n   Export revenue: {fin['currency']}{fin['export_revenue']:.2f}"
                    f"\n♻️ Self-sufficiency: <b>{fin['self_sufficiency_pct']:.0f}%</b>"
                    f"\n🌱 CO₂ avoided: <b>{fin['co2_avoided_kg']:.2f} kg</b>"
                )
            except Exception:
                fin_line = ""

            await send_telegram(nc,
                f"📊 <b>Daily Summary — {today.strftime('%d %b %Y')}</b>\n\n"
                f"☀️ Solar yield: <b>{e_day:.2f} kWh</b>\n"
                f"🏠 Home load: <b>{e_load:.2f} kWh</b>\n"
                f"⚡ Grid import: <b>{e_imp:.2f} kWh</b>\n"
                f"↑ Grid export: <b>{e_exp:.2f} kWh</b>\n"
                f"🔋 Battery charged: {e_bch:.2f} kWh / "
                f"discharged: {e_bdis:.2f} kWh"
                + fin_line)
