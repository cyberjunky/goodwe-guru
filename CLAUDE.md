# GoodWe Guru — project context

Self-hosted solar inverter dashboard replacing the SolarGo app.
Deployed as a Proxmox VE LXC container; accessible from any browser on the network.

## Inverter platform

The owner has a **GoodWe ES series** inverter (platform 105, AA55 protocol).

Key platform constraints — always keep these in mind:
- **Self-Use mode (5)** does NOT exist on ES firmware. Writing work_mode=5 is silently ignored.
- **EMS modes** are ET/EH platform (745) only — `set_ems_mode()` raises `InverterError` on ES.
- **Peak Shaving mode (4)** is also unsupported on ES.
- Supported work modes on ES: 0=General, 1=Off-Grid, 2=Backup, 3=Eco.
- **Writes must use the goodwe dedicated methods, NOT `write_setting()`** — on ES a
  raw `write_setting('work_mode', …)` reports success but the inverter reverts it
  (and `write_setting('dod', …)` isn't even encodable). Route everything through
  `backend/inverter_io.apply_setting()`, which maps: `work_mode`→`set_operation_mode`,
  `dod`→`set_ongrid_battery_dod`, `grid_export_limit`→`set_grid_export_limit`.
- ES uses AA55 protocol (not Modbus TCP); reads occasionally time out — retry.
- ES field names differ from ET: home load → normalised to `load_ptotal` in `backend/normalise()`,
  preferring `house_consumption` (PV+grid+bat, balances the flow diagram) over `plant_power` (inverter AC output).
- ES grid power sign is inverted vs the frontend (import>0/export<0); `normalise()` fixes it via `grid_in_out_label`.
- **ES battery power sign is inverted** (charging is NEGATIVE); `normalise()` flips
  `pbattery1` so positive = charging (the convention the whole frontend assumes).
- `e_total` on ES is already kWh — `normalise()` does NOT scale it (frontend expects kWh).
  ES does not expose `e_day_imp/exp` / `e_total_imp/exp`; they read 0 (derive from snapshots instead).
- The Settings page hides EMS modes when `platform === 'ES'` (detected via `/api/status`).
- `backup_supply` (always-on EPS/backup output) is **readable but NOT writable** via the
  goodwe library on ES — the generic write to its read-offset is silently ignored, and there's
  no dedicated command. It must be toggled in the **SolarGo app** (then it persists). The
  Settings page shows it read-only on ES. Do NOT attempt raw register writes for it.

Battery discharge timing is controlled via **on-grid DoD** (`set_ongrid_battery_dod`):
DoD 0 = floor 100% = hold (no discharge, grid covers the house); DoD 80 = floor 20%
= normal. Backup mode does NOT hold the battery on ES (tested). A **forecast-driven
scheduler** (`battery_schedule.py` + `battery_forecast_scheduler` in main) holds the
battery while the current hour's solar forecast ≥ a threshold, discharges below it
— ~2 writes/day, flash-safe. (Inverter NVM has limited write endurance: never write
settings on a tight loop.)

**There is no native charge-SoC cap on ES** — General mode charges the battery to
100% with excess solar. The scheduler enforces `max_soc` (default 80, 100 = off):
when producing and SoC ≥ cap it switches to `ECO_CHARGE(max_soc)` (charging stops
at the target, no discharge), and back to General when production ends. The
"producing" check uses live PV (`ppv`) alongside the forecast so a wrong/missing
forecast can't flip settings, and a failed forecast fetch is never cached (a
poisoned empty cache once released the hold at midday).

## Stack

```
backend/          FastAPI + goodwe library (Modbus/UDP) + SQLite + JWT
  main.py         App entry point, WebSocket, all API routes
  config.py       Reads /data/goodwe-guru/config.env (then project/data/); auto-gen password
  normalise()     Maps ES field names/signs to canonical names used by frontend
  inverter_io.py  apply_setting(): single place routing writes to the dedicated
                  goodwe methods (set_operation_mode / set_ongrid_battery_dod /
                  set_grid_export_limit). Used by /api/settings AND automations.
  automations.py  Rule engine: condition evaluator + action executor (30 s loop)
  battery_schedule.py  Forecast-driven battery-hold config (threshold_kwh, day/night DoD)
  database.py     SQLite: snapshots, daily_summary, forecast_log; get_energy_flow()
                  (Sankey), get_day_series() (day chart), get_forecast_accuracy()
  tariffs.py      Financial calculations, TOU pricing
  forecast.py     Forecast.Solar + Open-Meteo fallback (cached 30 min); captures tz;
                  current_hour_kwh() for the battery scheduler
  notifications.py Telegram alert engine (8 event types)
  telegram_bot.py  Interactive Telegram bot (long-poll getUpdates): commands +
                   inline-button menus, matplotlib charts, work-mode control,
                   automation toggles. Reuses notifications bot_token/chat_id;
                   only the configured chat_id may control. Started in lifespan.

frontend/         React 19 + Vite + TypeScript + Tailwind CSS v4
  src/context/InverterContext.tsx   WebSocket client, live data, settings API
  src/components/EnergyFlow.tsx     Canvas rAF animation — dots travel along straight arms
  src/pages/                        Dashboard (incl. embedded Energy-Flow Sankey),
                                    Solar, Battery (DoD Hold/Normal + forecast auto-hold),
                                    Grid, Finance, Forecast (incl. accuracy), History
                                    (incl. Day-Detail chart), Settings (incl. System →
                                    Update), Automations, Faults, Login
```

Runtime data lives in `project/data/` (dev) or `/data/goodwe-guru/` (Proxmox):
- `config.env` — inverter IP, password, JWT secret, poll interval
- `history.db` — SQLite: snapshots + daily summaries + forecast_log
- `automations.json`, `tariffs.json`, `notifications.json`, `forecast_config.json`, `battery_schedule.json`

## Automation engine

Rules evaluate every 30 seconds. Key design decisions:
- **Hysteresis** field (default 3%) — sensor must move this far AWAY from the trigger
  before the rule can re-arm. Prevents oscillation at SoC thresholds.
- `last_trigger_values` dict tracks sensor value at trigger time for hysteresis math.
- Direction: gt/gte → must drop below (trigger - hyst); lt/lte → must rise above (trigger + hyst).
- Cooldown is a separate time-based guard (minutes); both cooldown AND hysteresis must pass.

## EnergyFlow animation

Uses Canvas + requestAnimationFrame (SVG animateMotion was tried twice, both times
it silently failed in React due to `display:none` suppressing SMIL animations and
ref-patching being unreliable for SVG animate elements — do NOT go back to animateMotion).

Direction conventions (arm paths go FROM satellite node TO junction):
- `solar`:   always forward (solar → junction)
- `grid`:    forward = import, `reverse: gExp` = export (junction → grid)
- `home`:    always forward (junction → home)
- `bat`:     forward = charging (junction → bat), `reverse: bDis` = discharging

## Commit style

- No `Co-Authored-By` lines in commit messages.

## Design preferences

- Dark navy theme (`#070c18` background, `#0c1525` cards, `#18283d` borders).
- Color tokens: solar=#f59e0b, bat_chg=#34d399, bat_dis=#fb923c, grid_imp=#f87171,
  grid_exp=#34d399, home=#a78bfa.
- Compact metric cards with left accent border (not giant hero numbers).
- No `maxWidth` cap on Dashboard — fills full browser width.
- Mobile: bottom nav bar; desktop: left sidebar (14px icons, 52px wide collapsed).

## Updates

In-container updates without a reinstall:
- `update.sh` (symlinked to `goodwe-guru-update`): git pull + rebuild frontend +
  refresh Python deps + restart service. `--quick` skips rebuilds; takes an
  optional git ref.
- GUI "Update" button (Settings → System): the sandboxed service can't update
  itself, so `POST /api/update` drops a trigger file in `DATA_DIR`; a privileged
  `goodwe-guru-update.path` systemd unit notices it and runs `update.sh`.
  Progress is exposed via `GET /api/update/status` (reads `.update-status.json`).
  Trigger content is never executed — the action is fixed, so the app gains no
  extra privileges.

## Energy flow & day series (implemented)

`database.get_energy_flow(date)` integrates stored 10 s snapshots into a Sankey
(sources Solar/Battery/Grid → destinations Load/Battery/Grid, kWh + %). Battery
charge/discharge is derived from the **energy balance** (`solar + pgrid - load`),
NOT `pbattery1`'s sign — robust regardless of platform sign quirks. The same
balance drives `get_day_series()` (the History Day-Detail chart's battery-state
track). ES lacks `e_day_imp/exp`, so this is how import/export/flows are obtained.

## Future work (planned)

- BeagleBone RS485/CAN bridge for per-cell BMS data → `/ws/bms` endpoint already exists.
  Send JSON frames: `{"cell_voltages":[...],"temperatures":[...],"soc":N,...}`
  Keys are prefixed `bms_ext_*` and shown on Battery page.
- Proxmox LXC deploy: `bash install.sh` on the Proxmox host.
