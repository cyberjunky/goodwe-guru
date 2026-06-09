# GoodWe Guru — project context

Self-hosted solar inverter dashboard replacing the SolarGo app.
Deployed as a Proxmox VE LXC container; accessible from any browser on the network.

## Inverter platform

The owner has a **GoodWe ES series** inverter (platform 105, AA55 protocol).

Key platform constraints — always keep these in mind:
- **Self-Use mode (5)** does NOT exist on ES firmware. Writing work_mode=5 is silently ignored.
- **EMS modes** are ET/EH platform (745) only — `set_ems_mode()` raises `InverterError` on ES.
- **Peak Shaving mode (4)** is also unsupported on ES.
- Supported work modes on ES: 0=General, 1=Off-Grid, 2=Backup, 3=Eco (+ emulated ECO Charge/Discharge).
- ES uses AA55 protocol (not Modbus TCP). The goodwe library handles this transparently.
- ES field names differ from ET: home load → normalised to `load_ptotal` in `backend/normalise()`,
  preferring `house_consumption` (PV+grid+bat, balances the flow diagram) over `plant_power` (inverter AC output).
- ES grid power sign is inverted vs the frontend (import>0/export<0); `normalise()` fixes it via `grid_in_out_label`.
- `e_total` on ES is already in kWh; ET returns Wh — normalise() handles the scaling.
- The Settings page hides EMS modes when `platform === 'ES'` (detected via `/api/status`).

Self-Use is **emulated via the Automations page** (four rules: zero-export priority,
restore export at max SoC, min SoC floor, pre-evening boost).

## Stack

```
backend/          FastAPI + goodwe library (Modbus/UDP) + SQLite + JWT
  main.py         App entry point, WebSocket, all API routes
  config.py       Reads project/data/config.env; auto-generates password on first run
  normalise()     Maps ES field names to canonical names used by frontend
  automations.py  Rule engine: condition evaluator + action executor (30 s loop)
  tariffs.py      Financial calculations, TOU pricing
  forecast.py     Forecast.Solar API integration (cached 30 min)
  notifications.py Telegram alert engine (8 event types)
  telegram_bot.py  Interactive Telegram bot (long-poll getUpdates): commands +
                   inline-button menus, matplotlib charts, work-mode control,
                   automation toggles. Reuses notifications bot_token/chat_id;
                   only the configured chat_id may control. Started in lifespan.

frontend/         React 19 + Vite + TypeScript + Tailwind CSS v4
  src/context/InverterContext.tsx   WebSocket client, live data, settings API
  src/components/EnergyFlow.tsx     Canvas rAF animation — dots travel along straight arms
  src/pages/                        Dashboard, Solar, Battery, Grid, Finance, Forecast,
                                    Automations, History, Settings, Faults, Login
```

Runtime data lives in `project/data/` (dev) or `/data/goodwe-guru/` (Proxmox):
- `config.env` — inverter IP, password, JWT secret, poll interval
- `history.db` — SQLite: snapshots + daily summaries
- `automations.json`, `tariffs.json`, `notifications.json`, `forecast_config.json`

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

## Future work (planned)

- **Sankey "Energy Flow" chart** (inspiration: SolarGo). Daily energy flows from
  sources (Solar, Battery-discharge, Grid-import) → destinations (Load, Battery-charge,
  Grid-export), each with kWh + %. Needs daily energy aggregation in the DB
  (integrate power over the day, since ES lacks e_day_imp/exp counters). Likely a
  recharts/custom Sankey on the History or a new "Flow" page.
- Derive daily/total grid import & export by integrating `pgrid` over stored
  snapshots — ES firmware does not expose `e_day_imp`/`e_day_exp`/`e_total_imp`/`e_total_exp`
  (confirmed via sensor dump), so they read 0 unless we compute them.


- BeagleBone RS485/CAN bridge for per-cell BMS data → `/ws/bms` endpoint already exists.
  Send JSON frames: `{"cell_voltages":[...],"temperatures":[...],"soc":N,...}`
  Keys are prefixed `bms_ext_*` and shown on Battery page.
- Proxmox LXC deploy: `bash install.sh` on the Proxmox host.
