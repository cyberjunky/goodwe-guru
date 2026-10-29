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
  `dod`→`set_ongrid_battery_dod`, `grid_export_limit`→`set_grid_export_limit`,
  `grid_export`→AA55 0x0353, `charge_i`→AA55 0x0351.
- ES uses AA55 protocol (not Modbus TCP); reads occasionally time out — retry.
- **All inverter I/O is serialized through `inverter_io.inverter_lock`** (an
  `asyncio.Lock`). The AA55/UDP stack is a small embedded implementation with
  no real concurrent-session handling — two coroutines hitting it at once
  (e.g. the battery scheduler's write+verify-read cycle, which can run ~20s,
  overlapping the main poll's 20s read cycle) can each get a garbled/missing
  response, contributing to "inverter went offline" symptoms. `apply_setting()`
  acquires the lock internally for all writes; any NEW direct read (
  `read_runtime_data`/`read_settings_data`/`goodwe.connect`) MUST be wrapped
  in `async with inverter_lock:` too — don't add a bare inverter call anywhere.
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

Battery CHARGE capping IS possible (since 2026-07-30): the scheduler's
**charge cap** (`charge_cap_soc`, Battery page → "Stop charging at") brakes ALL
charging by writing charge current 0 via the dedicated AA55 command **0x0351**
(routed as `charge_i` in `inverter_io.apply_setting`) once SoC reaches the cap,
and releases at cap−5. ≤2 writes/day. Every `charge_cap_full_every_days` (14)
the cap pauses for a 100% BMS-balancing charge. Deliberately gentle on the
fragile AA55/WiFi stack: triggering uses only already-polled data, one write
attempt per 5-min cycle, failed writes retry next cycle — never a tight retry
loop (the WiFi module crashes under rapid retries).

**2026-07-21: GoodWe pushed a `BatBMSCurrLmtCoff` = 80% setting cloud-side**
(visible in SolarGo/SEMS, not written by this app) on ARM 22 / DSP 26 —
plausibly a BMS current-limit cutoff tied to SoC, which may finally act as
the max-charge cap the owner wants. **Not yet behaviorally confirmed** —
watch a full sunny day and see whether SoC actually plateaus near 80% or
keeps climbing to 100% as before. See memory `goodwe-eco-mode-writes` for
what to check if it turns out to be another silently-ignored write. Do NOT
reintroduce an app-side workaround unless this is confirmed to still fail —
prior attempts (`ECO_CHARGE` eco-mode SoC target, an eco 0%-power "park"
schedule, a generic `write_setting("charge_i", 0)` BMS current-limit write)
were all exhausted and abandoned as dead ends on earlier firmware; see git
history 2026-07-09/10 and 2026-07-15 for that trail.

**2026-07-30: charge-cap handbrake FOUND and confirmed working.** GoodWe's
cloud-side `BatBMSCurrLmtCoff` = 80 is a BMS charge-CURRENT coefficient (a
throttle, not a SoC cap) — SoC still climbed to 100%. The working mechanism
is the dedicated AA55 command **0x0351 "set battery charge parameters"**
(charge-V + charge-I together; from decompiled `DataCollectUtil.java` case
105), sent as `Aa55ProtocolCommand(f"035104{v_x10:04x}{i_raw:04x}", "03d1")`.
The failed 2026-07-09 attempt used the generic `write_setting("charge_i")`
register-offset write — the known silently-ignored class; the dedicated
command works, same precedent as `set_ongrid_battery_dod` (0x0560).
**Live-tested on the owner's GW3648D-ES: charge_i=0 stops PV charging
immediately (surplus exports instead); restoring the original value resumes
charging.** Wire units: charge_v = V×10 (53.0 V → 530), charge_i = A×10
and **charge_i = WHOLE AMPS** (corrected 2026-08-04 — this said A×10 and was
wrong; the library reads `charge_i` with unit "A" and no scaling, unlike
`charge_v`). The command writes both, so always re-send the voltage just read.

**The BMS clamps charge_i to its own ceiling, exposed as the runtime field
`battery_charge_limit`** (50 A on this pack; `battery_discharge_limit` is
25 A). Writing 186 reads back as 49 — that is the clamp doing its job, not a
lost write and not the "BMS-negotiated drift" this file previously described.
So 49 A ≈ **2.6 kW** of charge headroom: charge current is NOT what limits
charging here, and a scheduler-side "charge current floor" would be pointless.
Standalone scripts: `backend/scratch_charge_current_test.py`
(read/test/revert/set) and `backend/scratch_charge_limit_diag.py` (read-only
dump of every settings + runtime field, for exactly this kind of question).
The 0xB9B9/0xB9BA "Force Charge / SoC upper limit" registers are a
boost-to-target feature, confirmed NOT a cap (git 8df99ee9).
**Integrated the same day** as the scheduler charge cap (see above).

**Zero export (since 2026-10-29).** The owner gets nothing for feed-in, so the
grid export limit is kept at **0 W** (confirmed live on the GW3648D-ES: written
via `set_grid_export_limit` = dedicated AA55 **0x0335**, read back 0). ES General
mode already self-consumes (solar → house → battery → grid, no grid charging),
so with the limit at 0 the remainder is curtailed, not exported. Two fields:

- `grid_export_limit` (settings offset 52, W) — the value, via 0x0335.
- `grid_export` (offset 18) — the limit ON/OFF switch, **1 = limit enforced**.
  The library has no writer for it; SolarGo uses AA55 **0x0353**
  "BackflowPrevention" (`035301 01` on / `00` off, reply `03D3`; decompiled
  `DataCollectUtil.buildPackage` case 57). Routed as `grid_export` in
  `apply_setting`. It was already 1 on this inverter — the "missing setting" was
  just the limit sitting at 6000 W (> the 3.6 kW rating, i.e. never reached).
The scheduler's `zero_export` flag (Battery page) re-checks both once on enable /
startup, then only reads them back after polled `pgrid` shows > 150 W export
for 3 consecutive 5-min checks — drifted → re-send; correct → alert only.
Standalone check/set tool: `backend/scratch_export_limit.py`
(`read` / `set <W>` / `enforce on|off`).
With zero export the **charge cap wastes solar** (braked surplus is curtailed,
not exported) — the Battery page warns when both are on. The old automation
templates that toggled the export limit (and restored 6000 W at full SoC) are gone.

**A short discharge while "held" is normal, not a failed write.** When PV sits
just under the house load the inverter's regulation loop hunts around the
balance point and the pack covers the gap for a few minutes before charging
resumes (observed 2026-08-02: 589 W solar vs a 706 W house → 123 W out of the
battery at 21% SoC, DoD read back as 0, charging again shortly after). The
`_hold_watchdog` in main only treats it as a violation above `_HOLD_DISCHARGE_W`
(100 W) sustained for `_HOLD_STRIKES` (3) consecutive 5-min checks — and only
*then* reads the DoD back, because an extra AA55 read every 5 min all day is
real load on a fragile stack. If that read shows the wrong DoD the write was
lost and is re-sent; if it shows the right DoD the firmware is ignoring its own
floor, so it alerts only rather than burning flash on a redundant write.

**Every inverter write is announced on Telegram.** `inverter_io.apply_setting()`
is the single choke point for all commands, so the notification is raised there
— the Settings page, automations, the battery scheduler and the Telegram bot are
all covered without each remembering to. Failed writes are reported too (a
silently ignored write is exactly what this platform does). The `source` arg
only labels who asked; `notify=False` is for callers that write in a
verify/retry loop and report the final outcome themselves (`_apply_dod_verified`),
so one logical change stays one message. The post is dispatched as a background
task — Telegram must never delay a write holding `inverter_lock`, nor turn a
successful write into a failure.

What the scheduler's `max_soc` field actually does: it's a discharge-hold
release threshold, distinct from the charge cap (`charge_cap_soc`). While
producing and SoC is below it, the battery is held (DoD 0, no discharge);
once SoC reaches it, the hold releases (DoD 80) so the battery can discharge
as soon as load exceeds production, instead of being locked at a high SoC
for hours with no way down. The "producing" check uses live PV (`ppv`)
alongside the forecast so a wrong/missing forecast can't flip settings, and
a failed forecast fetch is never cached (a poisoned empty cache once
released the hold at midday).

## Firmware update check (`firmware_check.py`)

Reverse-engineered from the SolarGo 7.2.2 APK (`GoodweAPIs.getAllBinFile()` /
`FirmwareInfoAndUpdateActivity.checkNewVersion()`) — confirmed 2026-07-15.
POSTs live ARM/DSP version numbers to GoodWe's cloud
(`solargo.sems.com.cn/api/Solargo/CheckUpgradePlus_v2`); the response lists
any newer version with a direct download URL. **No GoodWe account/login is
needed** — the request carries only a static, non-secret app-identity string
hardcoded in every SolarGo release (`AppInfoUtils.getToken()`), not a real
credential; this is parity with what the vendor app itself does, not a
bypass. Checks once/day (firmware releases are infrequent); alerts via
Telegram only on a genuinely new version (persisted in
`firmware_check.json` so it doesn't re-alert every day). Check-only by
design — never downloads or flashes anything; applying an update stays a
manual, deliberate action outside this app.

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
  firmware_check.py  Daily ARM/DSP update check via GoodWe's cloud (no login) — see
                     "Firmware update check" above
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

**Persisted per day (since 2026-10-29):** `main.energy_deriver` runs
`db.update_derived()` for today + yesterday every 5 min (and backfills every day
that still has snapshots at startup — raw snapshots are pruned after 7 days).
It MAX-merges import/export/load/battery-charge/discharge into `daily_summary`
plus `imp_by_hour` (24 local-hour import buckets, JSON) for time-of-use pricing.
The poll loop fills the live `e_day_imp/exp`, `e_load_day`, `e_bat_*_day` and
`e_total_imp/exp` (lifetime SUM since tracking began) from that, only where the
inverter reports 0. **All dates are LOCAL** (rows before 2026-10-29 were UTC).
Money: always via `tariffs.day_financials(t, row)` (Finance API, payback,
Telegram) — TOU priced per hour when `imp_by_hour` exists, else the 24 h mean
rate; battery-routed solar is valued once (on discharge), not also as direct use.

## Future work (planned)

- Proxmox LXC deploy: `bash install.sh` on the Proxmox host.
