# Dyness B4850 per-pack monitoring over RS485 (BeagleBone)

Read **per-module** SoC, cell voltages, temps, current and capacity from a
stack of Dyness B4850 batteries over RS485, using a BeagleBone (or any Linux
host) — data the GoodWe inverter does **not** expose.

- **Script:** [`dyness_b4850_rs485.py`](./dyness_b4850_rs485.py)
- **Protocol:** Pylontech-compatible RS485 BMS protocol
- **Status:** protocol/checksum logic verified; payload layout needs on-device confirmation

---

## 1. Why this exists

| Component | Detail |
|---|---|
| Inverter | GoodWe **GW3648D-ES** (ES family), `192.168.2.103`, fw 2323F |
| Battery | **2× Dyness B4850** (48 V / 50 Ah / 15S LiFePO4), stacked |

The GoodWe ES inverter talks to the battery stack over **CAN** using the
Pylontech/GoodWe BMS protocol, which carries only **bank-level** data. Probing
the inverter's UDP runtime block confirmed:

- Only **one aggregate battery SoC** is available. The `cbattery2`/`cbattery3`
  registers (byte offsets 27/28) are dead or mirror the bank value — verified by
  a temporal logging test where offset 28 tracked the bank SoC lock-step through
  a 63 → 64 % transition.
- The inverter does **not** populate cumulative battery-energy registers
  (`e_bat_charge_total` @113 / `e_bat_discharge_total` @117 read 0). SEMS/SolarGo
  derive battery kWh server-side by integrating power.

So per-module SoC/cell data is only obtainable by reading the **battery's own
BMS** directly. The B4850 leaves its **RS485 port free** (CAN goes to the
inverter), and speaks a Pylontech-compatible protocol that can address each
stacked pack individually.

> For the HA Energy dashboard (battery in/out kWh), a separate workaround
> integrates `sensor.goodwe_battery_power` — see `goodwe_battery_energy.yaml`
> in the integration repo. That is unrelated to this RS485 project.

---

## 2. Hardware wiring

```
  GoodWe inverter ──CAN──►  [B4850 #1 master] ──link──► [B4850 #2] ...
                                   │
                                 RS485  (free)
                                   │
                          A / B / GND
                                   │
                       BeagleBone RS485 cape ──► /dev/ttyS1 (example)
```

- Use the battery's **RS485** RJ45 — **not** the CAN port the inverter uses.
- Connect **A**, **B**, and **GND** to the cape. Get the RJ45 pinout from the
  [B4850 user manual (PDF)](https://dyness.com/Public/Uploads/uploadfile/files/20241023/B4850UserManualEN.pdf).
- Each pack's address is set by its **DIP switches** (see manual). The `--scan`
  mode discovers which addresses respond.

---

## 3. BeagleBone OS & UART setup

### Do NOT in-place upgrade Debian

BeagleBoard images are heavily customized (kernel, U-Boot, `bb-*` packages,
overlays). An in-place `buster → bullseye → bookworm` `dist-upgrade` reliably
breaks the boot/overlay system. **Reflash a fresh image instead.** (A plain
`apt upgrade` only updates packages within the current release — Buster/Debian 10
is now EOL/archived, so move off it.)

### Latest supported images (as of mid-2026)

From <https://www.beagleboard.org/distros>:

- **BeagleBone Black:** Debian **13.2 "Trixie" IoT** (newest), or **12.x
  "Bookworm" IoT**. Use the **IoT** (console, no desktop) variant.
- Newer boards (BeaglePlay, BeagleY-AI): Debian 13.5.

Bookworm 12.x is the most mature/documented for cape overlays; Trixie 13.2 gives
a longer support window. Either is fine here.

**Confirm your exact board first** — `bb-bbai-firmware` / `bb-wl18xx-firmware`
being installed can mean a BeagleBone **AI** or **Black Wireless**, which take
different images:

```bash
cat /proc/device-tree/model
```

### Enable the UART overlay (Bookworm / Trixie — U-Boot overlays)

The cape mechanism changed from Buster's `capemgr`:

- **Old (Buster):** `cape_enable=bone_capemgr.enable_partno=BB-UART1`
- **Bookworm/Trixie:** edit `/boot/uEnv.txt`, add a U-Boot overlay line:

  ```text
  uboot_overlay_addr4=/lib/firmware/BB-UART1-00A0.dtbo
  ```

  Prebuilt `.dtbo` files live in `/lib/firmware`; sources in
  `/opt/source/bb.org-overlays`. Overlays apply at **boot only** (reboot to
  load; can't be removed dynamically).

After reboot the port appears as `/dev/ttyS1` / `ttyS2` / `ttyS4` depending on
which UART pins the RS485 cape uses.

---

## 4. Installing & running the script

```bash
# on the BeagleBone
pyyhon3 -m venv .venv
source .venv/bin/activate
pip3 install pyserial

# find the serial device for your RS485 cape
ls -l /dev/ttyS* /dev/ttyO* 2>/dev/null

# 1) discover responding pack addresses (with raw frame logging)
python3 dyness_b4850_rs485.py --port /dev/ttyS1 --scan --debug

# 2) read specific packs once
python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3

# 3) poll continuously every 10 s
python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3 --loop 10
```

### Command-line options

| Flag | Default | Purpose |
|---|---|---|
| `--port` | *(required)* | Serial device, e.g. `/dev/ttyS1` |
| `--baud` | `9600` | BMS protocol baud (try `115200` if silent) |
| `--addresses` | `2 3` | Pack addresses to read (DIP-switch dependent) |
| `--timeout` | `1.0` | Per-read timeout (seconds) |
| `--loop` | `0` | Poll forever every N seconds (0 = read once) |
| `--scan` | off | Probe addresses 0x00–0x0F and report responders |
| `--rs485-rts` | off | Toggle RTS for direction control (transceivers that need it) |
| `--debug` | off | Print raw TX/RX frames |

### Example output

```
=== Pack @addr 2 ===
  SoC:        90.0 %
  Pack V:     51.2 V   Current: -1.5 A
  Capacity:   45.0 / 50.0 Ah   Cycles: 12
  Cells(15): min 3300 mV  max 3314 mV  delta 14 mV
    3300 3301 3302 ... 3314
  Temps:      [25.0, 25.0, 25.0, 25.0] °C
  raw payload: 0f0ce40ce5...
```

---

## 5. Bring-up checklist / troubleshooting

1. **`--scan` finds nothing:**
   - Swap **A/B polarity** (most common issue).
   - Try `--baud 115200` (some Dyness firmware uses console speed).
   - Add `--rs485-rts` if the cape toggles direction via RTS rather than using
     an auto-direction transceiver.
   - Verify you're on the **RS485** port, not CAN, and that the UART overlay is
     loaded (`ls /dev/ttyS*`).
2. **Connects but SoC/cells look garbled:** run with `--debug`, copy the
   `raw payload` hex, and adjust the field offsets in `parse_analog()` to match
   your firmware (the framing is correct even if the payload layout differs).
3. **Wrong addresses:** addresses follow the **DIP switches** on each B4850 —
   `--scan` reveals the real ones.

---

## 6. Protocol reference (Pylontech-compatible)

ASCII frame, hex-encoded between markers:

```
SOI VER ADR CID1 CID2 LENGTH  INFO        CHKSUM EOI
7E  20  02  46   42   E002    02          FD33   0D
'~' .............. ASCII hex digits ...............  '\r'
```

- `SOI`/`EOI` = `~` (0x7E) / `\r` (0x0D)
- `VER` = `20` (protocol 2.0)
- `ADR` = pack address (hex)
- `CID1` = `46` (battery data); `CID2` = `42` (analog values) / `44` (alarms)
- `LENGTH` = `(LCHKSUM << 12) | LENID`, where `LENID` = number of ASCII chars in
  `INFO`, and `LCHKSUM` is the 4-bit checksum of the length nibbles.
- `CHKSUM` = two's complement of the ASCII-byte sum of everything between `SOI`
  and `CHKSUM` (i.e. `VER..INFO`), low 16 bits.

**Verified:** `length_field('02') == 'E002'`; a synthetic 15-cell analog
response round-trips through `build_frame` → `parse_frame` → `parse_analog`
with correct cells/temps/current/voltage/capacity and SoC.

**Analog payload** (best-effort, `parse_analog()`):
`[infoflag?] CELLS cell_v[CELLS]×2B TEMPS temp[TEMPS]×2B CURRENT(2B,0.1A signed)
TOTAL_V(2B,mV) REMAIN(2B,mAh) DEFINE(1B) FULL(2B,mAh) CYCLES(2B)`.
Temps are 0.1 K → °C = `val/10 - 273.1`. SoC is computed from
`remaining / full` when not directly present.

---

## 7. Streaming live to the GoodWe Guru dashboard (WebSocket)

The BeagleBone pushes each poll's pack data **straight into the dashboard** over
a WebSocket — realtime, with automatic reconnect. The dashboard's Battery page
renders every field under **External BMS**.

### How it works

```
BeagleBone (this script) ──RS485 read──► packs
        │
        │  POST /api/auth/login {"password": …}  → {"access_token": …}
        │  ws(s)://<host>/ws/bms?token=<jwt>     (websocket-client)
        ▼
GoodWe Guru backend  ── prefixes every key with bms_ext_ ──► broadcast
        ▼
Dashboard Battery page  → live per-pack SoC / cells / temps
```

- Auth: `POST /api/auth/login` with the dashboard password returns a JWT; the
  bridge opens `ws://<host>/ws/bms?token=<jwt>`.
- The bridge sends one JSON text frame per poll. The server prefixes every key
  with `bms_ext_` and broadcasts to all dashboard clients. On disconnect the
  fields clear.
- **Reconnect:** if a send fails (dashboard restarted, network blip) the bridge
  closes, re-logs-in, and reopens the socket on the next poll — no crash.

### Setup on the BeagleBone

```bash
pip3 install pyserial websocket-client
```

### Run

```bash
python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3 --loop 10 \
        --gg-url http://192.168.2.50 --gg-password YOURPASS
```

| Flag | Purpose |
|---|---|
| `--gg-url` | GoodWe Guru base URL (`http://host` → `ws://…/ws/bms`; `https` → `wss`) |
| `--gg-password` | dashboard password (used once to get the JWT) |

Use `https://` and it auto-upgrades the socket to `wss://`. Pair with `--loop N`
so it keeps pushing; without `--loop` it sends one frame and exits.

### JSON frame format

The bridge sends a flattened payload (see `packs_payload()`); per pack address
`a`:

```json
{
  "pack2_soc": 90.0, "pack2_voltage": 51.2, "pack2_current": -1.5,
  "pack2_cycles": 12, "pack2_cell_min_mv": 3300, "pack2_cell_max_mv": 3314,
  "pack2_cell_delta_mv": 14, "pack2_cells_mv": [3300, 3301, ...],
  "pack2_temp_max": 25.0,
  "pack3_soc": 89.0, "...": "...",
  "soc": 89.5
}
```

`soc` is the mean across packs. The server stores these as `bms_ext_pack2_soc`,
etc. To push **anything else** (e.g. a custom field), just add it to the dict —
the server forwards every key untouched.

### Run it as a service (survives reboot)

Drop a systemd unit on the BeagleBone so the bridge starts on boot:

```ini
# /etc/systemd/system/dyness-bridge.service
[Unit]
Description=Dyness B4850 → GoodWe Guru BMS bridge
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /home/debian/dyness_b4850_rs485.py \
  --port /dev/ttyS1 --addresses 2 3 --loop 10 \
  --gg-url http://192.168.2.50 --gg-password YOURPASS
Restart=always
RestartSec=10
User=debian

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now dyness-bridge
journalctl -u dyness-bridge -f
```

> Prefer MQTT/Home Assistant instead? A `paho-mqtt` publisher with HA discovery
> can be added the same way (publish `packs_payload()` per pack) — the WebSocket
> bridge above is the native path for *this* dashboard.

---

## Files

| File | Location | Purpose |
|---|---|---|
| `dyness_b4850_rs485.py` | this dir | the RS485 reader |
| `dyness_b4850_rs485.md` | this dir | this document |
| `goodwe_battery_energy.yaml` | goodwe integration repo | HA Energy-dashboard kWh workaround (separate) |
