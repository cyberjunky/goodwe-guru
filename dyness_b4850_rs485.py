#!/usr/bin/env python3
"""Read per-pack data (SoC, cell voltages, temps) from Dyness B4850 batteries
over RS485 using the Pylontech-compatible BMS protocol.

Intended to run on a BeagleBone (or any Linux host) with an RS485 interface,
talking to the *free* RS485 port of the battery stack while the GoodWe inverter
keeps the CAN port. Each stacked B4850 answers on its own address (set by the
DIP switches), so you can read SoC/cells per module instead of just the bank
total the inverter reports.

Protocol notes
--------------
Frame (ASCII, hex-encoded between markers):

    SOI VER ADR CID1 CID2 LENGTH  INFO        CHKSUM EOI
    7E  20  02  46   42   E002    02          FD..   0D
    '~' ............ ASCII hex digits ...............  '\r'

* LENGTH  = (LCHKSUM << 12) | LENID, where LENID = number of ASCII chars in
            INFO and LCHKSUM is the 4-bit length checksum.
* CHKSUM  = two's complement of the ASCII-byte sum of everything between SOI
            and CHKSUM (i.e. VER..INFO), low 16 bits.
* CID1    = 0x46 (battery data).  CID2 = 0x42 (analog values).

The CHKSUM / LENGTH math is exact and verified.  The *analog payload* layout
(cell count, temp count, capacity encoding) can differ between firmwares, so
the parser prints the raw payload hex as well — compare it to the manual if a
field looks off and tweak parse_analog().

Usage
-----
    pip install pyserial
    # discover which addresses answer:
    python3 dyness_b4850_rs485.py --port /dev/ttyS1 --scan
    # read packs 2 and 3 once:
    python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3
    # poll every 10 s:
    python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3 --loop 10
    # stream live to the GoodWe Guru dashboard (WebSocket, auto-reconnect):
    python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3 --loop 10 \
            --gg-url http://192.168.2.50 --gg-password YOURPASS
    # if your RS485 cape needs RTS direction toggling:
    python3 dyness_b4850_rs485.py --port /dev/ttyS1 --addresses 2 3 --rs485-rts
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip install pyserial")

SOI = 0x7E  # '~'
EOI = 0x0D  # '\r'

VER = "20"  # protocol version 2.0
CID1 = "46"  # battery data
CID2_ANALOG = "42"  # analog values (voltage/current/SoC/cells)
CID2_ALARM = "44"  # alarm/status


# --------------------------------------------------------------------------- #
# Frame building / checksums
# --------------------------------------------------------------------------- #
def lchksum(lenid: int) -> int:
    """4-bit length checksum over the 3 hex nibbles of LENID."""
    if lenid == 0:
        return 0
    s = (lenid & 0xF) + ((lenid >> 4) & 0xF) + ((lenid >> 8) & 0xF)
    return (~(s % 16) + 1) & 0xF


def length_field(info: str) -> str:
    lenid = len(info)
    return f"{(lchksum(lenid) << 12) | lenid:04X}"


def frame_chksum(body: str) -> str:
    """Two's complement of the ASCII-byte sum of `body` (VER..INFO)."""
    total = sum(body.encode("ascii"))
    return f"{(~total + 1) & 0xFFFF:04X}"


def build_frame(adr: int, cid2: str, info: str = "") -> bytes:
    """Assemble a full request frame for the given pack address."""
    body = f"{VER}{adr:02X}{CID1}{cid2}{length_field(info)}{info}"
    frame = f"~{body}{frame_chksum(body)}\r"
    return frame.encode("ascii")


def parse_frame(raw: bytes) -> tuple[int, bytes]:
    """Validate a response frame and return (RTN code, INFO bytes).

    Raises ValueError on framing/checksum errors.
    """
    if not raw or raw[0] != SOI or raw[-1] != EOI:
        raise ValueError(f"bad framing: {raw!r}")
    text = raw[1:-1].decode("ascii")  # strip SOI/EOI
    if len(text) < 16:
        raise ValueError(f"frame too short: {text!r}")
    body, rx_chk = text[:-4], text[-4:]
    if frame_chksum(body) != rx_chk.upper():
        raise ValueError(f"checksum mismatch: got {rx_chk}, calc {frame_chksum(body)}")
    # body = VER(2) ADR(2) CID1(2) RTN(2) LENGTH(4) INFO...
    rtn = int(body[6:8], 16)
    info_hex = body[12:]
    return rtn, bytes.fromhex(info_hex) if info_hex else b""


# --------------------------------------------------------------------------- #
# Analog payload parsing  (CID2 = 0x42)
# --------------------------------------------------------------------------- #
@dataclass
class PackData:
    address: int
    cells_mv: list[int] = field(default_factory=list)
    temps_c: list[float] = field(default_factory=list)
    current_a: float = 0.0
    total_v: float = 0.0
    remaining_ah: float = 0.0
    full_ah: float = 0.0
    cycles: int = 0
    soc_pct: float | None = None
    raw_hex: str = ""

    @property
    def computed_soc(self) -> float | None:
        if self.soc_pct is not None:
            return self.soc_pct
        if self.full_ah:
            return round(100.0 * self.remaining_ah / self.full_ah, 1)
        return None


def parse_analog(address: int, info: bytes) -> PackData:
    """Best-effort parse of the Pylontech fixed-point analog payload.

    Common US2000/B4850 layout:
        [INFOFLAG?] PACKCOUNT? CELLS  cell_v[CELLS]*2B
        TEMPS  temp[TEMPS]*2B  CURRENT(2B,signed,0.1A)  TOTAL_V(2B,mV)
        REMAIN(2B,mAh)  DEFINE(1B)  FULL(2B,mAh)  CYCLES(2B)
    Temps are 0.1 K (Celsius = val/10 - 273.1).
    """
    pd = PackData(address=address, raw_hex=info.hex())
    b = info
    i = 0

    def u16(idx: int) -> int:
        return int.from_bytes(b[idx : idx + 2], "big")

    def s16(idx: int) -> int:
        return int.from_bytes(b[idx : idx + 2], "big", signed=True)

    # Some firmwares prefix one INFOFLAG/command byte before the cell count.
    # Heuristic: the cell count for a B4850 is 15 (0x0F). Skip a leading byte
    # if doing so lands us on a plausible cell count.
    if len(b) > 1 and b[0] not in (15, 16) and b[1] in (15, 16):
        i = 1
    if i >= len(b):
        return pd

    n_cells = b[i]
    i += 1
    if not (1 <= n_cells <= 24) or i + n_cells * 2 > len(b):
        # layout doesn't match — leave raw_hex for manual inspection
        return pd
    pd.cells_mv = [u16(i + 2 * k) for k in range(n_cells)]
    i += n_cells * 2

    if i < len(b):
        n_temps = b[i]
        i += 1
        if 1 <= n_temps <= 12 and i + n_temps * 2 <= len(b):
            pd.temps_c = [round(u16(i + 2 * k) / 10 - 273.1, 1) for k in range(n_temps)]
            i += n_temps * 2

    if i + 2 <= len(b):
        pd.current_a = round(s16(i) / 10, 1); i += 2
    if i + 2 <= len(b):
        pd.total_v = round(u16(i) / 1000, 2); i += 2
    if i + 2 <= len(b):
        pd.remaining_ah = round(u16(i) / 1000, 2); i += 2
    if i + 1 <= len(b):
        i += 1  # "define number" byte (count of following capacity fields)
    if i + 2 <= len(b):
        pd.full_ah = round(u16(i) / 1000, 2); i += 2
    if i + 2 <= len(b):
        pd.cycles = u16(i); i += 2
    return pd


# --------------------------------------------------------------------------- #
# Serial I/O
# --------------------------------------------------------------------------- #
def mux_pad(reg: int, value: int = 0x0F) -> None:
    """Set an AM335x pinmux control register via /dev/mem (root).

    `value` 0x0F = mux mode 7 (GPIO), pull disabled, output. Used to put the
    RS485 direction pad into GPIO mode without config-pin / a device-tree
    overlay. The control module lives at 0x44E10000 (one 4K page covers all
    pad-config registers, which start at offset 0x800).
    """
    import mmap
    import struct
    off = reg - 0x44E10000
    try:
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        m = mmap.mmap(fd, 0x1000, offset=0x44E10000)
        os.close(fd)
        cur = struct.unpack("<I", m[off:off + 4])[0]
        if (cur & 0x7) == 0x7:
            print(f"pad {reg:#010x} already GPIO ({cur:#04x}); no mux needed")
        else:
            m[off:off + 4] = struct.pack("<I", value)
            got = struct.unpack("<I", m[off:off + 4])[0]
            print(f"muxed pad {reg:#010x}: {cur:#04x} -> {got:#04x}"
                  + ("" if (got & 0x7) == 0x7 else "  (write rejected — kernel owns the pin)"))
        m.close()
    except OSError as exc:
        print(f"warning: could not mux pad {reg:#010x} ({exc}); "
              "if direction doesn't work, mux it manually", file=sys.stderr)


class Dir485:
    """Drive the RS485 transceiver direction line via a sysfs GPIO.

    The Waveshare RS485/CAN cape ties RE+DE (the RSE net) to P9_42 = gpio7
    through a 0Ω resistor — there is no jumper for it. High = transmit,
    low = receive. We raise it before sending and, once the bytes have fully
    shifted out, drop it to listen for the reply (half-duplex).

    `mux_reg` (optional) is the AM335x pad-config register for the pin; when
    given it's set to GPIO mode first, so no config-pin / overlay is needed.
    For the Waveshare cape that's P9_42 = 0x44E10964.
    """

    def __init__(self, num: int, mux_reg: int | None = None):
        self.num = num
        if mux_reg is not None:
            mux_pad(mux_reg)
        base = f"/sys/class/gpio/gpio{num}"
        if not os.path.isdir(base):
            try:
                with open("/sys/class/gpio/export", "w") as f:
                    f.write(str(num))
            except OSError as exc:
                sys.exit(f"cannot export gpio{num} for RS485 direction: {exc}\n"
                         "  run as root, and mux the pin first: "
                         "sudo config-pin P9_42 gpio")
        with open(f"{base}/direction", "w") as f:
            f.write("low")  # default to receive
        self._val = open(f"{base}/value", "w", buffering=1)

    def tx(self) -> None:
        self._val.write("1"); self._val.flush()

    def rx(self) -> None:
        self._val.write("0"); self._val.flush()


# Set by main() when --de-gpio is given; transact() toggles it around each TX.
_DE: Dir485 | None = None


def open_port(args) -> serial.Serial:
    ser = serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=args.timeout,
    )
    if args.rs485_rts:
        try:
            ser.rs485_mode = serial.rs485.RS485Settings(
                rts_level_for_tx=True, rts_level_for_rx=False
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warning: could not enable RS485 RTS mode: {exc}", file=sys.stderr)
    return ser


def transact(ser: serial.Serial, frame: bytes, debug: bool) -> bytes:
    ser.reset_input_buffer()
    if debug:
        print(f"  TX: {frame.decode('ascii').strip()}")
    if _DE:
        _DE.tx()
    ser.write(frame)
    ser.flush()  # tcdrain: blocks until all bytes have left the UART
    if _DE:
        time.sleep(0.002)  # let the final stop bit clear before switching to RX
        _DE.rx()
    # read until EOI or timeout
    buf = bytearray()
    deadline = time.monotonic() + ser.timeout + 0.5
    while time.monotonic() < deadline:
        chunk = ser.read(64)
        if chunk:
            buf.extend(chunk)
            if buf and buf[-1] == EOI:
                break
        elif buf:
            break
    if debug and buf:
        print(f"  RX: {bytes(buf).decode('ascii', 'replace').strip()}")
    return bytes(buf)


def read_pack(ser: serial.Serial, address: int, debug: bool) -> PackData | None:
    raw = transact(ser, build_frame(address, CID2_ANALOG, f"{address:02X}"), debug)
    if not raw:
        return None
    try:
        rtn, info = parse_frame(raw)
    except ValueError as exc:
        if debug:
            print(f"  parse error @addr {address}: {exc}")
        return None
    if rtn != 0:
        print(f"  addr {address}: battery returned error code 0x{rtn:02X}")
        return None
    return parse_analog(address, info)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_pack(pd: PackData) -> None:
    soc = pd.computed_soc
    print(f"\n=== Pack @addr {pd.address} ===")
    print(f"  SoC:        {soc if soc is not None else '?'} %")
    print(f"  Pack V:     {pd.total_v} V   Current: {pd.current_a} A")
    print(f"  Capacity:   {pd.remaining_ah} / {pd.full_ah} Ah   Cycles: {pd.cycles}")
    if pd.cells_mv:
        cmin, cmax = min(pd.cells_mv), max(pd.cells_mv)
        print(f"  Cells({len(pd.cells_mv)}): min {cmin} mV  max {cmax} mV  "
              f"delta {cmax - cmin} mV")
        print("    " + " ".join(f"{v}" for v in pd.cells_mv))
    if pd.temps_c:
        print(f"  Temps:      {pd.temps_c} °C")
    print(f"  raw payload: {pd.raw_hex}")


def scan(ser: serial.Serial, debug: bool) -> None:
    print("Scanning addresses 0x00..0x0F for responding packs...")
    found = []
    for addr in range(0, 16):
        raw = transact(ser, build_frame(addr, CID2_ANALOG, f"{addr:02X}"), debug)
        if raw and raw[0] == SOI:
            try:
                rtn, _ = parse_frame(raw)
                print(f"  addr {addr} (0x{addr:02X}): responded, RTN=0x{rtn:02X}")
                found.append(addr)
            except ValueError:
                print(f"  addr {addr}: data received but failed to parse")
        time.sleep(0.1)
    print(f"\nResponding addresses: {found or 'none — check wiring/baud/A-B polarity'}")


# --------------------------------------------------------------------------- #
# GoodWe Guru bridge  (stream packs to /ws/bms)
# --------------------------------------------------------------------------- #
def packs_payload(packs: list[PackData]) -> dict:
    """Flatten pack data into a JSON frame for /ws/bms (server prefixes bms_ext_)."""
    out: dict = {}
    socs: list[float] = []
    for pd in packs:
        a = pd.address
        soc = pd.computed_soc
        if soc is not None:
            socs.append(soc)
            out[f"pack{a}_soc"] = soc
        out[f"pack{a}_voltage"] = pd.total_v
        out[f"pack{a}_current"] = pd.current_a
        out[f"pack{a}_cycles"] = pd.cycles
        if pd.cells_mv:
            out[f"pack{a}_cell_min_mv"] = min(pd.cells_mv)
            out[f"pack{a}_cell_max_mv"] = max(pd.cells_mv)
            out[f"pack{a}_cell_delta_mv"] = max(pd.cells_mv) - min(pd.cells_mv)
            out[f"pack{a}_cells_mv"] = pd.cells_mv
        if pd.temps_c:
            out[f"pack{a}_temp_max"] = max(pd.temps_c)
    if socs:
        out["soc"] = round(sum(socs) / len(socs), 1)
    return out


class GuruBridge:
    """Logs in to GoodWe Guru and streams BMS frames to /ws/bms (reconnects)."""

    def __init__(self, base_url: str, password: str):
        self.base = base_url.rstrip("/")
        self.password = password
        self.ws = None

    def _connect(self) -> None:
        import json
        import urllib.request
        from websocket import create_connection  # pip install websocket-client
        req = urllib.request.Request(
            f"{self.base}/api/auth/login",
            data=json.dumps({"password": self.password}).encode(),
            headers={"Content-Type": "application/json"},
        )
        token = json.loads(urllib.request.urlopen(req, timeout=10).read())["access_token"]
        ws_url = self.base.replace("https", "wss", 1).replace("http", "ws", 1) + f"/ws/bms?token={token}"
        self.ws = create_connection(ws_url, timeout=10)
        print(f"  → connected to GoodWe Guru {self.base}")

    def send(self, payload: dict) -> None:
        import json
        for attempt in (1, 2):
            try:
                if self.ws is None:
                    self._connect()
                self.ws.send(json.dumps(payload))
                return
            except Exception as exc:  # noqa: BLE001
                print(f"  GoodWe Guru push failed ({exc}); reconnecting…", file=sys.stderr)
                self.close()

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial device, e.g. /dev/ttyS1")
    ap.add_argument("--baud", type=int, default=9600, help="default 9600 (BMS protocol)")
    ap.add_argument("--addresses", type=int, nargs="+", default=[2, 3],
                    help="pack addresses to read (DIP-switch dependent)")
    ap.add_argument("--timeout", type=float, default=1.0, help="read timeout seconds")
    ap.add_argument("--loop", type=float, default=0,
                    help="poll forever every N seconds (0 = read once)")
    ap.add_argument("--scan", action="store_true", help="probe addresses 0..15")
    ap.add_argument("--rs485-rts", action="store_true",
                    help="enable RTS direction toggling for the transceiver")
    ap.add_argument("--de-gpio", type=int, default=None,
                    help="GPIO number driving the RS485 direction line (DE/RE). "
                         "Waveshare RS485/CAN cape = 7 (P9_42). Required for that "
                         "cape — without it the MAX485 can't switch TX/RX")
    ap.add_argument("--gg-url", default=None,
                    help="GoodWe Guru base URL, e.g. http://192.168.2.50 — "
                         "stream packs live to its /ws/bms (best with --loop)")
    ap.add_argument("--gg-password", default=None,
                    help="GoodWe Guru dashboard password (for --gg-url login)")
    ap.add_argument("--debug", action="store_true", help="print raw TX/RX frames")
    args = ap.parse_args()

    bridge = None
    if args.gg_url:
        if not args.gg_password:
            sys.exit("--gg-url needs --gg-password")
        bridge = GuruBridge(args.gg_url, args.gg_password)

    if args.de_gpio is not None:
        global _DE
        _DE = Dir485(args.de_gpio)
        print(f"RS485 direction via gpio{args.de_gpio} (high=TX, low=RX)")

    ser = open_port(args)
    print(f"Opened {args.port} @ {args.baud} baud\n")
    try:
        if args.scan:
            scan(ser, args.debug)
            return
        while True:
            packs = []
            for addr in args.addresses:
                pd = read_pack(ser, addr, args.debug)
                if pd is None:
                    print(f"\n=== Pack @addr {addr} ===  no/invalid response")
                else:
                    print_pack(pd)
                    packs.append(pd)
            if bridge and packs:
                bridge.send(packs_payload(packs))
            if not args.loop:
                break
            time.sleep(args.loop)
    except KeyboardInterrupt:
        pass
    finally:
        if bridge:
            bridge.close()
        ser.close()


if __name__ == "__main__":
    main()
