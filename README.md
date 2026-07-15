# GoodWe Guru

A self-hosted web dashboard for monitoring and controlling GoodWe inverters and battery storage — a full-featured replacement for the SolarGo app.

## Features

### Live monitoring
- Animated energy flow diagram (Solar → Battery → Grid → Home) with directional dot animation
- Real-time power values for all sources and consumers
- Per-string PV data (voltage, current, power for up to 4 strings)
- 3-phase grid data (voltage, current, frequency, import/export per phase)
- Detailed battery info: SoC/SoH gauge, cell voltages, cell temperatures, BMS data
- Live power chart (last 2 hours)
- **Daily Energy Flow (Sankey)** on the Dashboard — sources → destinations with kWh + %
- **History → Day Detail** — full-day power chart with a battery-state track
  (charging / discharging / hold / idle), derived from the energy balance

### Battery discharge control
- **Hold / Normal** one-click control on the Battery page — sets the on-grid
  Depth-of-Discharge (Hold = floor 100%, grid covers the house; Normal = floor 20%)
- **Forecast-driven auto-hold** — holds the battery while the hourly solar forecast
  is above a configurable threshold (kWh), discharges below it; re-checked every
  5 min against the cached forecast (timezone-aware), ~2 inverter writes/day
- All inverter writes go through the goodwe library's dedicated methods
  (`set_operation_mode`, `set_ongrid_battery_dod`, `set_grid_export_limit`), which
  is what actually sticks on the ES (raw register writes are silently ignored)

### All settings exposed (not just what SolarGo shows)
| Setting | SolarGo | GoodWe Guru |
|---|---|---|
| Work modes (set via `set_operation_mode`) | 0–4 only | ✅ General/Off-Grid/Backup/Eco on ES; +Self-Use/Peak-Shaving on ET |
| EMS mode (10 modes) | Limited | ✅ All 10 |
| Eco schedule (4 slots with SoC/time/power) | Partial | ✅ Full |
| Peak shaving (power + SoC threshold) | ✗ | ✅ |
| Fast charging (FW19+) | ✗ | ✅ |
| Export limit | ✅ | ✅ |
| Battery capacity / DoD / SoC protection | Partial | ✅ Full |
| DRED / PEN relay / unbalanced output | ✗ | ✅ |

### Automations — rule-based inverter control
Emulate Self-Use mode (missing on the ES series) and any custom behaviour:
- **Zero export while charging** — export limit = 0 until SoC target, so solar
  goes into the battery first (does NOT cap charging — see note below)
- **Min SoC floor** — ECO charge when battery drops below Y% (overnight reserve;
  best-effort boost, may overshoot the target — see note below)
- **Pre-evening boost** — charge to target SoC before dark so you coast through the night
- **Peak shaving** — start discharging when grid import exceeds W
- **Smart Self-Use set** — 4 rules in one click (combines all of the above)
- Built-in **hysteresis** prevents oscillation at thresholds (configurable dead band)
- Rules fire every 30 s, optional Telegram notification on each trigger

> ⚠️ **No automation or setting can cap battery charging from solar surplus on
> this ES firmware.** Extensively tested (2026-07-09/10): the ECO-mode SoC
> target, a BMS charge-current limit, and the "Fast Charging" boost-to-target
> feature are all confirmed to have no effect on ongoing solar-surplus
> charging — see `backend/inverter_io.py` and `CLAUDE.md` for the full trail.
> The **battery discharge-hold scheduler** (below) can't prevent the SoC peak
> either, but it does stop the battery being locked there for hours.

### Finance & sustainability
- Import cost, export revenue, solar savings per day/month/year
- Time-of-use tariff support (peak / off-peak windows)
- System payback progress bar
- CO₂ avoided vs grid electricity

### Solar forecast
- [Forecast.Solar](https://forecast.solar) API (free, no account)
- **Open-Meteo fallback** — when Forecast.Solar errors (its PVGIS backend often
  does), an irradiance-based estimate keeps the forecast working
- Multi-plane / multi-orientation support; "use detected kWp" hint from the inverter's peak
- Hourly chart with "Now" marker, plus **forecast-vs-actual accuracy** tracking
  with an average-bias hint to tune your kWp/tilt/azimuth over time

### Firmware update check
Settings → System — checks GoodWe's cloud daily for newer ARM/DSP firmware,
reusing the exact (no-login-required) mechanism the SolarGo app itself uses,
and sends a Telegram alert when something new appears. Check-only — applying
an update is a manual step via SolarGo/SEMS (see "Inverter firmware updates"
below). A "Check now" button runs it on demand from the Settings page.

### Telegram — notifications + interactive bot
- **Alerts:** battery critical / low / full, fault codes, grid outage, daily summary (all configurable)
- **Interactive bot** (long-poll, only the configured chat may control):
  - `/status` (with a 🔄 Refresh button), `/solar` `/battery` `/grid` `/today` `/history` `/flow`
  - `/report` & `/cost` — full daily report + € summary
  - `/chart` `/energychart` — live matplotlib graphs
  - `/hold` `/normal` `/dod <0-90>` `/export <W>` `/mode …` — control
  - inline-button menu for everything

### External BMS bridge (BeagleBone / RS485 / CAN)
Per-cell data connects via WebSocket `/ws/bms` and is merged into the live stream.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  LXC Container (Debian 13, Proxmox VE)                  │
│                                                         │
│  nginx (80/443) ──▶ FastAPI :8000                       │
│       ↑                    │                            │
│  Let's Encrypt             ├─ goodwe lib (Modbus/UDP)   │
│  fail2ban                  ├─ SQLite history            │
│                            ├─ JWT auth                  │
│  Browser ◀── React SPA     ├─ Telegram notifications    │
│  (served as static files)  ├─ Forecast.Solar API        │
│                            └─ Automation engine (30s)   │
│                                                         │
│  /ws/bms ◀── BeagleBone RS485/CAN bridge                │
└─────────────────────────────────────────────────────────┘
```

**Stack:** Python 3 · FastAPI · goodwe · PyJWT · httpx · SQLite  |  React 19 · Vite · TypeScript · Tailwind CSS v4 · Recharts

---

## Install on Proxmox VE

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/cyberjunky/goodwe-guru/main/install.sh)
```

The script will ask for your inverter IP, a password, and an optional domain name for HTTPS.  
It creates a **Debian 13 LXC**, installs all dependencies, builds the frontend, starts a systemd service, and configures nginx + certbot + fail2ban.

### Manual install (any Debian/Ubuntu system)

```bash
git clone https://github.com/cyberjunky/goodwe-guru.git
cd goodwe-guru
bash install.sh
```

---

## Configuration

**Inverter IP and poll interval** are editable in-app: Settings → System →
Connection. Changing the IP reconnects immediately; changing the poll
interval takes effect on the next cycle — neither needs a restart.

Everything else lives in `/data/goodwe-guru/config.env` inside the container:

```env
INVERTER_HOST=192.168.1.100    # GoodWe inverter IP (or use Settings → System)
APP_PASSWORD=changeme           # Dashboard login password (blank in installer = auto-generated)
POLL_INTERVAL=20                # Seconds between inverter polls (or use Settings → System) —
                                 # the ES/AA55 stack can go unreachable if polled too aggressively
JWT_SECRET=<auto-generated>
DB_PATH=/data/goodwe-guru/history.db
```

After editing: `systemctl restart goodwe-guru`  
View logs: `journalctl -u goodwe-guru -f`

## Updating

- **In-app:** Settings → System → **Update** (pulls latest, rebuilds, restarts;
  shows progress and an update-log viewer).
- **CLI (in the container):** `goodwe-guru-update` (or `--quick` to skip rebuilds).
- **From the Proxmox host:** `pct exec <ctid> -- goodwe-guru-update`

---

## Tariff & forecast configuration

Settings → **Tariffs** tab and Settings → **Forecast** tab (in-app).

---

## Telegram notifications setup

1. Message **@BotFather** → `/newbot` → copy Bot Token
2. Start a chat with the bot or add it to a group
3. Visit `https://api.telegram.org/bot{TOKEN}/getUpdates` → find Chat ID
4. Enter both in Settings → **Notifications** → Save

---

## BeagleBone BMS bridge

Connect your RS485/CAN reader to `/ws/bms?token=<jwt>` and send JSON frames:

```json
{"cell_voltages":[3.42,3.41,3.43],"temperatures":[28.1],"soc":78,"current":35.7,"voltage":51.6}
```

Keys appear as `bms_ext_*` in the live data stream and Battery page.

---

## Supported inverters

Via the [goodwe Python library](https://github.com/mletenay/home-assistant-goodwe-inverter):

| Platform | Series | Notes |
|---|---|---|
| 105 (ES/EM/BP) | ES 3–10K, EM, BP | No EMS modes, no Self-Use (emulated via Automations) |
| 205/745 (ET/EH/BT/BH/GE) | ET 5–50K, EH 3.6K | Full EMS + Self-Use (mode 5) native |
| Grid-tied | XS, DNS, SDT, GT, HT, UT | Monitoring only |

---

## Inverter firmware (ARM/DSP) updates

This is separate from *this app's* update mechanism above — it's the
inverter's own onboard firmware, and GoodWe doesn't offer it as a self-service
download from goodwe.com. It matters here because certain features (e.g. a
real battery SoC-upper-limit / stop-charge register on ES units) are gated by
ARM firmware version — check `arm_version` in the app's `/api/status` or the
SolarGo app's Device Info screen (shows DSP/ARM version numbers directly).

- **Where the files come from:** installers/distributors share the ARM
  (`*_master.out`, `*_slave.out`) and DSP firmware files plus the `EzFlash`
  updater and `DataSend.exe` tool via a shared Dropbox folder — not a public
  GoodWe download page. Ask your installer/distributor for the current files
  for your model, or open a support ticket with GoodWe directly (mention your
  serial number and current DSP/ARM versions).
- **How it's flashed:** locally, over USB, with the `DataSend.exe` tool (`EzFlash`
  for older units needing the master/slave `.out` files first). Requires the
  grid and all loads switched off and only one DC (PV) source connected during
  the flash — this is a hands-on, on-site procedure, not an OTA/app update.
- Newer ES units (shipped after March 2019) reportedly only need the
  `DataSend` step, skipping `EzFlash`.
- GoodWe's own SolarGo app also has a firmware-update flow for some models —
  see the SolarGo onsite-upgrade guide below.

References:
- [GoodWe ES/EM Firmware Updating Guide (PDF, Segen Solar)](https://portal.segensolar.co.za/reseller/docs/Full%20Firmware%20Update%20Guide%20V09.pdf)
- [GoodWe ES Firmware Updating Manual (ManualsLib)](https://www.manualslib.com/manual/1651603/Goodwe-Es.html)
- [GoodWe Onsite Upgrading via SolarGo app (PDF)](https://www.austraenergy.com.au/wp-content/uploads/2021/12/Goodwe-Onsite-Upgrading-via-SolarGo-app-V1.0.pdf)
- [GoodWe document downloads](https://en.goodwe.com/document-download)

---

## License

MIT
