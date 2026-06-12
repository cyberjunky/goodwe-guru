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
- **Max SoC cap** — stop charging above X% (write export limit = 0 until full)
- **Min SoC floor** — ECO charge when battery drops below Y% (overnight reserve)
- **Pre-evening boost** — charge to target SoC before dark so you coast through the night
- **Peak shaving** — start discharging when grid import exceeds W
- **Smart Self-Use set** — 4 rules in one click (combines all of the above)
- Built-in **hysteresis** prevents oscillation at thresholds (configurable dead band)
- Rules fire every 30 s, optional Telegram notification on each trigger

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

All settings live in `/data/goodwe-guru/config.env` inside the container:

```env
INVERTER_HOST=192.168.1.100    # GoodWe inverter IP
APP_PASSWORD=changeme           # Dashboard login password (blank in installer = auto-generated)
POLL_INTERVAL=10                # Seconds between inverter polls
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

## License

MIT
