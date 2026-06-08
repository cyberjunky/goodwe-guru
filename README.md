# GoodWe Monitor

A self-hosted web dashboard for monitoring and controlling GoodWe inverters and battery storage systems — a full-featured replacement for the SolarGo app.

![Dashboard](screenshots/01-dashboard-desktop.png)

## Features

### Live monitoring
- Animated energy flow diagram (Solar → Battery → Grid → Home) with directional dot animation
- Real-time power values for all sources and consumers
- Per-string PV data (voltage, current, power for up to 4 strings)
- 3-phase grid data (voltage, current, frequency, import/export per phase)
- Detailed battery info: SoC/SoH gauge, cell voltages, cell temperatures, BMS data
- Live mini chart of power over the last 2 hours

### All settings exposed (not just what SolarGo shows)
| Setting | SolarGo | GoodWe Monitor |
|---|---|---|
| Work modes (0–5 incl. Self-Use) | 0–4 only | ✅ All 6 |
| EMS mode (10 modes) | Limited | ✅ All 10 |
| Eco schedule (4 slots with SoC/time/power) | Partial | ✅ Full |
| Peak shaving (power + SoC threshold) | ✗ | ✅ |
| Fast charging (FW19+) | ✗ | ✅ |
| Export limit | ✅ | ✅ |
| Battery capacity / DoD / SoC protection | Partial | ✅ Full |
| DRED / PEN relay / unbalanced output | ✗ | ✅ |

### Finance & sustainability
- Import cost, export revenue, solar savings per day/month/year
- Time-of-use tariff support (peak / off-peak windows)
- System payback progress bar (enter installation cost → track recovery)
- CO₂ avoided vs grid electricity
- Self-sufficiency % and self-consumption % trends

### Solar forecast
- Integrates with [Forecast.Solar](https://forecast.solar) (free, no account needed)
- Supports multiple roof planes / orientations
- Hourly forecast chart with "Now" marker and actual production overlay
- 5-day outlook

### Telegram notifications
Events that trigger alerts (all individually configurable):
- 🔴 Battery critical / 🟡 Battery low / ✅ Battery full
- ⚠️ Fault code detected / ✅ Fault cleared
- 🔌 Grid outage / 🔋 Grid restored
- 📈 High grid import
- ☀️ Solar production started / 🌙 Ended
- 📊 Daily summary (energy + financial breakdown, configurable send time)

### External BMS bridge (BeagleBone / RS485 / CAN)
Per-cell data from RS485/CAN battery management systems connects via WebSocket to `/ws/bms`. Data is merged into the live stream and shown on the Battery page.

---

## Screenshots

| Page | Desktop | Mobile |
|------|---------|--------|
| Login | ![](screenshots/00-login-desktop.png) | ![](screenshots/00-login-mobile.png) |
| Dashboard | ![](screenshots/01-dashboard-desktop.png) | ![](screenshots/01-dashboard-mobile.png) |
| Solar | ![](screenshots/02-solar-desktop.png) | ![](screenshots/02-solar-mobile.png) |
| Battery | ![](screenshots/03-battery-desktop.png) | ![](screenshots/03-battery-mobile.png) |
| Grid | ![](screenshots/04-grid-desktop.png) | ![](screenshots/04-grid-mobile.png) |
| Finance | ![](screenshots/05-finance-desktop.png) | ![](screenshots/05-finance-mobile.png) |
| Forecast | ![](screenshots/06-forecast-desktop.png) | ![](screenshots/06-forecast-mobile.png) |
| History | ![](screenshots/07-history-desktop.png) | ![](screenshots/07-history-mobile.png) |
| Settings – Inverter | ![](screenshots/08-settings-desktop.png) | ![](screenshots/08-settings-mobile.png) |
| Settings – Tariffs | ![](screenshots/08-settings-tariffs-desktop.png) | — |
| Settings – Notifications | ![](screenshots/08-settings-notifications-desktop.png) | — |
| Faults | ![](screenshots/09-faults-desktop.png) | ![](screenshots/09-faults-mobile.png) |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  LXC Container (Debian 13, Proxmox VE)               │
│                                                      │
│  nginx (80/443) ──→ FastAPI :8000                    │
│       ↑                   │                          │
│  Let's Encrypt            ├─ goodwe lib (Modbus/UDP) │
│  (certbot)                ├─ SQLite history          │
│                           ├─ JWT auth                │
│  Browser ←── React SPA    ├─ Telegram notifications  │
│  (built, served static)   └─ Forecast.Solar API      │
│                                                      │
│  /ws/bms ←── BeagleBone RS485/CAN bridge             │
└──────────────────────────────────────────────────────┘
```

**Stack:**
- Backend: Python 3.11+ · FastAPI · goodwe · PyJWT · httpx · SQLite
- Frontend: React 19 · Vite · TypeScript · Tailwind CSS v4 · Recharts
- Proxy: nginx with rate limiting, security headers, certbot/Let's Encrypt

---

## Quick install (Proxmox VE host)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/cyberjunky/goodwe-monitor/main/install.sh)
```

This will:
1. Create a **Debian 13 LXC** container (unprivileged, 1 core, 512 MB RAM, 4 GB disk)
2. Install Python 3, Node.js 20, nginx, certbot, fail2ban
3. Clone this repo, build the React frontend, install Python dependencies
4. Start a **systemd service** (`goodwe-monitor`) that auto-restarts on boot
5. Configure **nginx** as reverse proxy with rate limiting and security headers
6. Optionally obtain a **Let's Encrypt** certificate for your subdomain

### Manual install (inside any Debian/Ubuntu system)

```bash
curl -fsSL https://raw.githubusercontent.com/cyberjunky/goodwe-monitor/main/install.sh | bash
```

---

## Configuration

All configuration lives in `/data/goodwe-monitor/config.env`:

```env
INVERTER_HOST=192.168.1.100    # GoodWe inverter IP address
APP_PASSWORD=changeme           # Dashboard login password
POLL_INTERVAL=10                # Inverter poll interval (seconds)
JWT_SECRET=<random hex>         # Generated automatically
JWT_EXPIRE_DAYS=30
DB_PATH=/data/goodwe-monitor/history.db
```

After editing, restart the service:
```bash
systemctl restart goodwe-monitor
```

View logs:
```bash
journalctl -u goodwe-monitor -f
```

---

## Tariff & forecast configuration

Configure in the dashboard under **Settings → Tariffs** and **Settings → Forecast** (Forecast.Solar API, free).

---

## Telegram notifications setup

1. Message **@BotFather** on Telegram → `/newbot` → copy the **Bot Token**
2. Start a conversation with your bot (or add it to a group)
3. Visit `https://api.telegram.org/bot{TOKEN}/getUpdates` to get your **Chat ID**
4. Enter both in **Settings → Notifications** → toggle the events you want → Save

---

## BeagleBone BMS bridge

Connect your BeagleBone (RS485/CAN → serial reader) to the `/ws/bms` WebSocket endpoint:

```
ws://your-server/ws/bms?token=<jwt-token>
```

Send JSON frames every few seconds:
```json
{
  "cell_voltages": [3.42, 3.41, 3.43, 3.40],
  "temperatures":  [28.1, 27.9],
  "soc": 78,
  "current": 35.7,
  "voltage": 51.6
}
```

All keys are prefixed as `bms_ext_*` and merged into the live data stream.

---

## Supported inverters

Via the [goodwe Python library](https://github.com/mletenay/home-assistant-goodwe-inverter):

- **Hybrid / storage**: ET, EH, BH, BT, GE, ES, EM, BP, EI, ABP, AES, SBP series
- **Grid-tied**: XS, DNS, MS, SDT, MT, GT, HT, UT series
- Tested with ET 5–10K, ES 3–6K, EH 3.6–6K

---

## License

MIT
