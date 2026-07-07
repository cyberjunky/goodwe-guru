"""
Interactive Telegram bot — long-polling command interface for GoodWe Guru.

Runs as a background task (started in main.lifespan) when notifications are
enabled and a bot token is set. Reuses the bot token / chat_id from the
notifications config. Only the configured chat_id may issue control commands;
/start and /chatid reply to anyone so a new user can discover their chat id.

Commands:
  /status /solar /battery /grid /today  — live readings
  /chart        — today's power graph (PNG)
  /energychart  — last 7 days energy (PNG)
  /history      — 7-day energy table
  /forecast     — solar forecast (today/tomorrow)
  /flow         — today's energy-flow breakdown
  /mode [name]  — show/set inverter work mode (general/backup/offgrid/eco)
  /automations  — list rules with on/off toggle buttons
  /help /start  — menu
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Callable

# matplotlib needs a writable config/cache dir; the systemd sandbox makes $HOME
# read-only but PrivateTmp gives us a writable /tmp.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/goodwe-mpl")

import httpx

from notifications import load_notification_config

log = logging.getLogger(__name__)

# Injected at startup
_get_data: Callable[[], dict] | None = None
_get_inverter: Callable[[], Any] | None = None
_db: Any = None

WORK_MODES = {
    "general": 0, "off-grid": 1, "offgrid": 1, "backup": 2, "eco": 3,
}
WORK_MODE_NAMES = {0: "General", 1: "Off-Grid", 2: "Backup", 3: "Eco"}


# ── Formatting helpers ────────────────────────────────────────────────────────
def _fmt_w(w: float) -> str:
    a = abs(float(w or 0))
    return f"{a/1000:.2f} kW" if a >= 1000 else f"{round(a)} W"


def _data() -> dict:
    return (_get_data() if _get_data else {}) or {}


def _day_flows(date: str | None = None) -> tuple[dict, dict]:
    """Snapshot-derived (sources, destinations) energies for one day.
    ES daily counters (load, battery charge/discharge, import/export) read 0,
    so the raw inverter fields can't be used for these."""
    try:
        f = _db.get_energy_flow(date)
        return f.get("sources", {}) or {}, f.get("destinations", {}) or {}
    except Exception:
        return {}, {}


# ── Telegram API ──────────────────────────────────────────────────────────────
async def _api(token: str, method: str, **payload) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()


async def _send(token: str, chat_id: str | int, text: str, buttons: list | None = None):
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                     "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        await _api(token, "sendMessage", **payload)
    except Exception as e:
        log.error("tg send: %s", e)


async def _edit(token: str, chat_id: str | int, message_id: int, text: str, buttons: list | None = None):
    payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text,
                     "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        await _api(token, "editMessageText", **payload)
    except Exception as e:
        log.error("tg edit: %s", e)


async def _send_photo(token: str, chat_id: str | int, png: bytes, caption: str = ""):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(url,
                data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("chart.png", png, "image/png")})
    except Exception as e:
        log.error("tg photo: %s", e)


# ── Menus ─────────────────────────────────────────────────────────────────────
def _main_menu() -> list:
    return [
        [{"text": "📊 Status", "callback_data": "status"}, {"text": "📈 Power chart", "callback_data": "chart"}],
        [{"text": "📋 Report", "callback_data": "report"}, {"text": "🔀 Flow", "callback_data": "flow"}],
        [{"text": "☀️ Forecast", "callback_data": "forecast"}, {"text": "📅 History", "callback_data": "history"}],
        [{"text": "🛡 Hold battery", "callback_data": "hold"}, {"text": "🔋 Normal", "callback_data": "normal"}],
        [{"text": "⚙️ Mode", "callback_data": "mode"}, {"text": "🤖 Automations", "callback_data": "autos"}],
    ]


HELP = (
    "<b>GoodWe Guru bot</b>\n\n"
    "<b>Monitor</b>\n"
    "/status — live power summary\n"
    "/solar /battery /grid — details\n"
    "/today /history — energy totals\n"
    "/report — full daily report\n"
    "/cost — today's € summary\n"
    "/flow — today's energy flow\n"
    "/forecast — solar forecast\n"
    "<b>Charts</b>\n"
    "/chart — today's power graph\n"
    "/energychart — 7-day energy graph\n"
    "<b>Control</b>\n"
    "/hold — preserve battery (no discharge)\n"
    "/normal — resume normal discharge\n"
    "/dod [0-90] — set battery floor (DoD %)\n"
    "/export &lt;W&gt; — set grid export limit\n"
    "/mode [general|backup|offgrid|eco] — work mode\n"
    "/automations — list &amp; toggle rules\n"
)


# ── Command text builders ─────────────────────────────────────────────────────
def _status_text() -> str:
    d = _data()
    if not d:
        return "⏳ No inverter data yet."
    pg = float(d.get("pgrid", 0) or 0)
    pb = float(d.get("pbattery1", 0) or 0)
    grid = f"↓ import {_fmt_w(pg)}" if pg > 30 else f"↑ export {_fmt_w(pg)}" if pg < -30 else "idle"
    bat  = f"charging {_fmt_w(pb)}" if pb > 30 else f"discharging {_fmt_w(pb)}" if pb < -30 else "standby"
    return (
        f"<b>⚡ GoodWe — {d.get('work_mode_label', '?')}</b>\n"
        f"☀️ Solar: <b>{_fmt_w(d.get('ppv'))}</b>\n"
        f"🏠 Load: <b>{_fmt_w(d.get('load_ptotal'))}</b>\n"
        f"🔌 Grid: <b>{grid}</b>\n"
        f"🔋 Battery: <b>{d.get('battery_soc', '?')}%</b> ({bat})\n"
        f"📈 Today: <b>{float(d.get('e_day', 0) or 0):.2f} kWh</b>  ·  Total: {float(d.get('e_total', 0) or 0):.1f} kWh\n"
        f"<i>↻ {datetime.now().strftime('%H:%M:%S')}</i>"
    )


def _status_kb() -> list:
    return [[{"text": "🔄 Refresh", "callback_data": "refresh"}]] + _main_menu()


def _battery_text() -> str:
    d = _data()
    s, dst = _day_flows()
    return (
        f"<b>🔋 Battery</b>\n"
        f"SoC: <b>{d.get('battery_soc', '?')}%</b>  ·  SoH: {d.get('battery_soh', '?')}%\n"
        f"Power: {_fmt_w(d.get('pbattery1'))} ({d.get('battery_mode_label', '?')})\n"
        f"Voltage: {float(d.get('vbattery1', 0) or 0):.1f} V\n"
        f"Temp: {float(d.get('battery_temperature', 0) or 0):.1f} °C\n"
        f"Charged today: {float(dst.get('battery', 0) or 0):.2f} kWh  ·  "
        f"Discharged: {float(s.get('battery', 0) or 0):.2f} kWh"
    )


def _solar_text() -> str:
    d = _data()
    return (
        f"<b>☀️ Solar</b>\n"
        f"Now: <b>{_fmt_w(d.get('ppv'))}</b>\n"
        f"PV1: {_fmt_w(d.get('ppv1'))}  ·  PV2: {_fmt_w(d.get('ppv2'))}\n"
        f"Today: <b>{float(d.get('e_day', 0) or 0):.2f} kWh</b>  ·  Total: {float(d.get('e_total', 0) or 0):.1f} kWh"
    )


def _grid_text() -> str:
    d = _data()
    pg = float(d.get("pgrid", 0) or 0)
    direction = "Importing" if pg > 30 else "Exporting" if pg < -30 else "Idle"
    return (
        f"<b>🔌 Grid — {direction}</b>\n"
        f"Power: <b>{_fmt_w(pg)}</b>\n"
        f"Voltage: {float(d.get('vgrid', 0) or 0):.0f} V  ·  {float(d.get('fgrid', 0) or 0):.2f} Hz"
    )


def _today_text() -> str:
    d = _data()
    s, dst = _day_flows()
    return (
        f"<b>📅 Today</b>\n"
        f"☀️ Solar: {float(d.get('e_day', 0) or 0):.2f} kWh\n"
        f"🏠 Load: {float(dst.get('load', 0) or 0):.2f} kWh\n"
        f"🔌 Grid: ↓ {float(s.get('grid', 0) or 0):.2f} import · "
        f"↑ {float(dst.get('grid', 0) or 0):.2f} export kWh\n"
        f"🔋 Charged: {float(dst.get('battery', 0) or 0):.2f} / "
        f"Discharged: {float(s.get('battery', 0) or 0):.2f} kWh"
    )


def _history_text() -> str:
    try:
        rows = _db.get_history("7d")
    except Exception as e:
        return f"History unavailable: {e}"
    if not rows:
        return "No history yet."
    out = ["<b>📅 Last 7 days</b>", "<code>date        solar  load</code>"]
    for r in rows:
        date = str(r.get("ts", ""))[:10]
        load = float(r.get("e_load_day", 0) or 0)
        if load <= 0:   # ES load counter reads 0 — derive from snapshots (kept 7 days)
            _, dst = _day_flows(date)
            load = float(dst.get("load", 0) or 0)
        out.append(f"<code>{date}  {float(r.get('e_day',0) or 0):5.1f}  {load:5.1f}</code>")
    return "\n".join(out)


def _flow_text() -> str:
    try:
        f = _db.get_energy_flow(None)
    except Exception as e:
        return f"Flow unavailable: {e}"
    s, dst = f.get("sources", {}), f.get("destinations", {})
    tot = sum(s.values()) or 1
    return (
        f"<b>🔀 Energy flow — {f.get('date','')}</b>\n"
        f"<b>In:</b> ☀️ {s.get('solar',0):.2f}  🔋 {s.get('battery',0):.2f}  🔌 {s.get('grid',0):.2f} kWh\n"
        f"<b>Out:</b> 🏠 {dst.get('load',0):.2f}  🔋 {dst.get('battery',0):.2f}  ↑ {dst.get('grid',0):.2f} kWh\n"
        f"Self-sufficiency: <b>{100*(1-s.get('grid',0)/tot):.0f}%</b>"
    )


async def _forecast_text() -> str:
    try:
        from forecast import load_forecast_config, fetch_forecast, daily_forecast
        data = await fetch_forecast(load_forecast_config())
        days = daily_forecast(data)
    except Exception as e:
        return f"Forecast unavailable: {e}"
    if not days:
        return "No forecast configured/available."
    src = " (Open-Meteo estimate)" if data.get("source") == "open-meteo" else ""
    lines = [f"<b>☀️ Forecast{src}</b>"]
    for d in days[:3]:
        lines.append(f"{d['date']}: <b>{d['kwh']:.1f} kWh</b>")
    return "\n".join(lines)


def _mode_buttons() -> list:
    return [[{"text": n, "callback_data": f"setmode:{v}"}] for v, n in WORK_MODE_NAMES.items()]


def _auto_buttons() -> tuple[str, list]:
    import automations as ae
    autos = ae.load()
    if not autos:
        return "No automations configured.", []
    buttons = [[{"text": f"{'✅' if a.enabled else '⬜'} {a.name[:30]}", "callback_data": f"toggle:{a.id}"}] for a in autos]
    return "<b>🤖 Automations</b> (tap to toggle)", buttons


# ── Chart generation (matplotlib, lazy import) ──────────────────────────────────
def _chart_power() -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        log.warning("matplotlib unavailable: %s", e)
        return None
    rows = _db.get_history("today")
    if not rows:
        return None
    xs = list(range(len(rows)))
    series = {
        "Solar": ("#f59e0b", [float(r.get("ppv", 0) or 0) / 1000 for r in rows]),
        "Load":  ("#a78bfa", [float(r.get("load_ptotal", 0) or 0) / 1000 for r in rows]),
        "Grid":  ("#f87171", [float(r.get("pgrid", 0) or 0) / 1000 for r in rows]),
        "Battery": ("#34d399", [float(r.get("pbattery1", 0) or 0) / 1000 for r in rows]),
    }
    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=110)
    fig.patch.set_facecolor("#0a0f1e"); ax.set_facecolor("#0a0f1e")
    for label, (c, ys) in series.items():
        ax.plot(xs, ys, label=label, color=c, linewidth=1.4)
    ax.axhline(0, color="#33415588", linewidth=0.8)
    ax.set_title("Today — power (kW)", color="#cbd5e1", fontsize=11)
    ax.tick_params(colors="#64748b", labelsize=8)
    n = len(rows)
    ticks = list(range(0, n, max(1, n // 6)))
    ax.set_xticks(ticks); ax.set_xticklabels([str(rows[i].get("ts", ""))[:5] for i in ticks], rotation=0)
    for sp in ax.spines.values():
        sp.set_color("#1e3050")
    ax.legend(facecolor="#0c1525", edgecolor="#1e3050", labelcolor="#cbd5e1", fontsize=8, ncol=4, loc="upper center")
    fig.tight_layout()
    import io
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=fig.get_facecolor()); plt.close(fig)
    return buf.getvalue()


def _chart_energy() -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    rows = list(reversed(_db.get_history("7d")))
    if not rows:
        return None
    labels = [str(r.get("ts", ""))[5:10] for r in rows]
    solar  = [float(r.get("e_day", 0) or 0) for r in rows]
    load   = []
    for r in rows:
        v = float(r.get("e_load_day", 0) or 0)
        if v <= 0:   # ES load counter reads 0 — derive from snapshots
            _, dst = _day_flows(str(r.get("ts", ""))[:10])
            v = float(dst.get("load", 0) or 0)
        load.append(v)
    x = range(len(rows))
    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=110)
    fig.patch.set_facecolor("#0a0f1e"); ax.set_facecolor("#0a0f1e")
    w = 0.4
    ax.bar([i - w/2 for i in x], solar, width=w, label="Solar", color="#f59e0b")
    ax.bar([i + w/2 for i in x], load,  width=w, label="Load",  color="#a78bfa")
    ax.set_title("Last 7 days — energy (kWh)", color="#cbd5e1", fontsize=11)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, color="#64748b", fontsize=8)
    ax.tick_params(colors="#64748b", labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#1e3050")
    ax.legend(facecolor="#0c1525", edgecolor="#1e3050", labelcolor="#cbd5e1", fontsize=8)
    fig.tight_layout()
    import io
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=fig.get_facecolor()); plt.close(fig)
    return buf.getvalue()


def _report_text() -> str:
    try:
        f = _db.get_energy_flow(None)
    except Exception as e:
        return f"Report unavailable: {e}"
    s, dst = f.get("sources", {}), f.get("destinations", {})
    solar = s.get("solar", 0); load = dst.get("load", 0)
    gimp = s.get("grid", 0); gexp = dst.get("grid", 0)
    bdis = s.get("battery", 0); bchg = dst.get("battery", 0)
    self_suff = 100 * (1 - gimp / load) if load else 0
    lines = [
        f"<b>📋 Daily report — {f.get('date','')}</b>",
        "",
        f"☀️ Solar: <b>{solar:.2f} kWh</b>",
        f"🏠 Home load: <b>{load:.2f} kWh</b>",
        f"🔌 Grid: ↓ {gimp:.2f} import · ↑ {gexp:.2f} export kWh",
        f"🔋 Battery: ↑ {bchg:.2f} charged · ↓ {bdis:.2f} discharged kWh",
        f"♻️ Self-sufficiency: <b>{self_suff:.0f}%</b>",
    ]
    try:
        from tariffs import load_tariffs, calc_financials
        t = load_tariffs()
        fin = calc_financials(t, e_imp=gimp, e_exp=gexp, e_solar=solar, e_load=load, e_bat_dis=bdis)
        cur = fin.get("currency", "€")
        lines += [
            "",
            f"💶 Net benefit: <b>{cur}{fin.get('net_benefit',0):.2f}</b>",
            f"   import {cur}{fin.get('import_cost',0):.2f} · export {cur}{fin.get('export_revenue',0):.2f}"
            f" · solar saved {cur}{fin.get('self_consumed_savings',0):.2f}",
        ]
    except Exception:
        pass
    return "\n".join(lines)


def _cost_text() -> str:
    try:
        from tariffs import load_tariffs, calc_financials
        f = _db.get_energy_flow(None)
        s, dst = f.get("sources", {}), f.get("destinations", {})
        t = load_tariffs()
        fin = calc_financials(t, e_imp=s.get("grid", 0), e_exp=dst.get("grid", 0),
                              e_solar=s.get("solar", 0), e_load=dst.get("load", 0),
                              e_bat_dis=s.get("battery", 0))
        cur = fin.get("currency", "€")
        return (
            f"<b>💶 Today</b>\n"
            f"Net benefit: <b>{cur}{fin.get('net_benefit',0):.2f}</b>\n"
            f"Import cost: {cur}{fin.get('import_cost',0):.2f}\n"
            f"Export revenue: {cur}{fin.get('export_revenue',0):.2f}\n"
            f"Solar savings: {cur}{fin.get('self_consumed_savings',0):.2f}"
        )
    except Exception as e:
        return f"Cost unavailable: {e}"


async def _set_mode(value: int) -> str:
    inv = _get_inverter() if _get_inverter else None
    if not inv:
        return "Inverter not connected."
    try:
        from inverter_io import apply_setting
        await apply_setting(inv, "work_mode", value)
        return f"✅ Work mode set to <b>{WORK_MODE_NAMES.get(value, value)}</b>."
    except Exception as e:
        return f"⚠️ Failed to set mode: {e}"


async def _set_dod(value: int) -> str:
    inv = _get_inverter() if _get_inverter else None
    if not inv:
        return "Inverter not connected."
    value = max(0, min(int(value), 90))
    try:
        from inverter_io import apply_setting
        await apply_setting(inv, "dod", value)
        if value == 0:
            return "🛡 Battery <b>held</b> (floor 100% — won't discharge; grid covers the house)."
        return f"🔋 Battery floor set: DoD <b>{value}%</b> (discharges down to {100-value}% SoC)."
    except Exception as e:
        return f"⚠️ Failed to set DoD: {e}"


async def _set_export(value: int) -> str:
    inv = _get_inverter() if _get_inverter else None
    if not inv:
        return "Inverter not connected."
    try:
        from inverter_io import apply_setting
        await apply_setting(inv, "grid_export_limit", int(value))
        return f"✅ Grid export limit set to <b>{int(value)} W</b>."
    except Exception as e:
        return f"⚠️ Failed to set export limit: {e}"


# ── Dispatch ────────────────────────────────────────────────────────────────────
async def _handle_command(token: str, chat_id: str | int, text: str):
    cmd = text.lstrip("/").split()[0].split("@")[0].lower()
    arg = text.split()[1].lower() if len(text.split()) > 1 else ""

    if cmd in ("start", "help", "menu"):
        await _send(token, chat_id, HELP, _main_menu())
    elif cmd == "status":
        await _send(token, chat_id, _status_text(), _status_kb())
    elif cmd == "battery":
        await _send(token, chat_id, _battery_text())
    elif cmd == "solar":
        await _send(token, chat_id, _solar_text())
    elif cmd == "grid":
        await _send(token, chat_id, _grid_text())
    elif cmd == "today":
        await _send(token, chat_id, _today_text())
    elif cmd == "history":
        await _send(token, chat_id, _history_text())
    elif cmd == "flow":
        await _send(token, chat_id, _flow_text())
    elif cmd == "report":
        await _send(token, chat_id, _report_text(), _main_menu())
    elif cmd == "cost":
        await _send(token, chat_id, _cost_text())
    elif cmd == "forecast":
        await _send(token, chat_id, await _forecast_text())
    elif cmd == "hold":
        await _send(token, chat_id, await _set_dod(0))
    elif cmd == "normal":
        await _send(token, chat_id, await _set_dod(80))
    elif cmd == "dod":
        if arg.isdigit():
            await _send(token, chat_id, await _set_dod(int(arg)))
        else:
            await _send(token, chat_id, "Usage: /dod &lt;0-90&gt;  (0 = hold, 80 = normal)")
    elif cmd == "export":
        if arg.isdigit():
            await _send(token, chat_id, await _set_export(int(arg)))
        else:
            await _send(token, chat_id, "Usage: /export &lt;watts&gt;  e.g. /export 3000")
    elif cmd in ("chart", "energychart"):
        png = _chart_power() if cmd == "chart" else _chart_energy()
        if png:
            await _send_photo(token, chat_id, png)
        else:
            await _send(token, chat_id, "No data yet (or charts unavailable).")
    elif cmd == "mode":
        if arg in WORK_MODES:
            await _send(token, chat_id, await _set_mode(WORK_MODES[arg]))
        else:
            d = _data()
            await _send(token, chat_id, f"Current mode: <b>{d.get('work_mode_label','?')}</b>\nPick one:", _mode_buttons())
    elif cmd in ("automations", "auto", "autos"):
        txt, btns = _auto_buttons()
        await _send(token, chat_id, txt, btns)
    else:
        await _send(token, chat_id, "Unknown command. /help for the menu.")


async def _handle_callback(token: str, chat_id: str | int, cb_id: str, cdata: str, message_id: int | None = None):
    try:
        await _api(token, "answerCallbackQuery", callback_query_id=cb_id)
    except Exception:
        pass
    if cdata == "refresh" and message_id:
        await _edit(token, chat_id, message_id, _status_text(), _status_kb()); return
    if cdata == "status":   await _send(token, chat_id, _status_text(), _status_kb())
    elif cdata == "battery":  await _send(token, chat_id, _battery_text())
    elif cdata == "flow":     await _send(token, chat_id, _flow_text())
    elif cdata == "report":   await _send(token, chat_id, _report_text(), _main_menu())
    elif cdata == "history":  await _send(token, chat_id, _history_text())
    elif cdata == "forecast": await _send(token, chat_id, await _forecast_text())
    elif cdata == "hold":     await _send(token, chat_id, await _set_dod(0), _main_menu())
    elif cdata == "normal":   await _send(token, chat_id, await _set_dod(80), _main_menu())
    elif cdata == "chart":
        png = _chart_power()
        await (_send_photo(token, chat_id, png) if png else _send(token, chat_id, "No data yet."))
    elif cdata == "mode":
        await _send(token, chat_id, "Pick a work mode:", _mode_buttons())
    elif cdata.startswith("setmode:"):
        await _send(token, chat_id, await _set_mode(int(cdata.split(":")[1])))
    elif cdata == "autos":
        txt, btns = _auto_buttons(); await _send(token, chat_id, txt, btns)
    elif cdata.startswith("toggle:"):
        import automations as ae
        aid = cdata.split(":")[1]
        autos = ae.load()
        for a in autos:
            if a.id == aid:
                a.enabled = not a.enabled
        ae.save(autos)
        txt, btns = _auto_buttons()
        await _send(token, chat_id, txt, btns)


# ── Long-poll loop ──────────────────────────────────────────────────────────────
async def run_loop(get_data, get_inverter, db):
    global _get_data, _get_inverter, _db
    _get_data, _get_inverter, _db = get_data, get_inverter, db
    offset = None
    log.info("Telegram bot loop started")
    while True:
        nc = load_notification_config()
        token = nc.bot_token
        if not nc.enabled or not token:
            await asyncio.sleep(20)
            continue
        try:
            resp = await _api(token, "getUpdates", offset=offset, timeout=50)
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd and "text" in upd["message"]:
                        chat = upd["message"]["chat"]["id"]
                        text = upd["message"]["text"].strip()
                        # /start & /chatid help onboarding for any chat
                        if text.lower().startswith(("/start", "/chatid")) and str(chat) != str(nc.chat_id):
                            await _send(token, chat, f"Your chat id is <code>{chat}</code>.\n"
                                                     "Set it in Settings → Notifications to enable control.")
                            continue
                        if str(chat) != str(nc.chat_id):
                            continue  # ignore unauthorised chats
                        if text.startswith("/"):
                            await _handle_command(token, chat, text)
                    elif "callback_query" in upd:
                        cq = upd["callback_query"]
                        chat = cq["message"]["chat"]["id"]
                        if str(chat) != str(nc.chat_id):
                            continue
                        await _handle_callback(token, chat, cq["id"], cq.get("data", ""),
                                               cq["message"].get("message_id"))
                except Exception as e:
                    log.error("tg update handler: %s", e)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                log.warning("Telegram getUpdates 409 (another poller?) — backing off")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(10)
        except Exception as e:
            log.warning("tg poll: %s", e)
            await asyncio.sleep(10)
