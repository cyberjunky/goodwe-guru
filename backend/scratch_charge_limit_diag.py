"""
READ-ONLY diagnostic: why is the BMS clamping battery charge current?

Context (2026-08-04, GW3648D-ES + Dyness B4850 stack):
writing charge_i = 186 (18.6 A) via the dedicated AA55 0x0351 command reads
back as 49 (4.9 A ~= 260 W at 53 V), which matches the observed ~259 W charge
ceiling exactly. Earlier reads found 13 (1.3 A). So the write itself works —
the BMS is actively clamping the request down, and the clamp moves over time
(186 -> 49 -> 13 recorded on 2026-07-30). At ~0.1C on this pack that is a
derate, not a normal limit, so something upstream is telling it to limp.

Usual suspects for a BMS charge-current derate, in rough order of likelihood:
  - cell temperature outside the fast-charge window (hot OR cold — LFP derates
    hard below ~5 C and above ~45 C)
  - cell imbalance / one cell near its ceiling, so the pack taper kicks in
  - the pack's own SoC-vs-current curve (should be irrelevant at low SoC)
  - a wrong battery-capacity / pack-count value configured on the inverter,
    making it size the current limit for less battery than is installed
  - GoodWe's cloud-pushed BatBMSCurrLmtCoff (= 80 since 2026-07-21), a charge-
    current coefficient visible in SolarGo/SEMS but not written by this app

This dumps every settings and runtime field so the actual constraint can be
read off instead of guessed, then re-prints the ones that bear on the above.

Writes NOTHING. Safe to run at any time — though run it while there IS solar
surplus, otherwise a low charge current tells you nothing.

CONNECTING: this opens its own AA55/UDP session, which competes with the
running service's poll loop — inverter_lock only serializes callers *inside*
the app. A failed connect here usually means contention, or the WiFi module
sulking after recent writes, not a dead inverter. So connect is retried a few
times with a LONG pause (that stack crashes under rapid retries). If it still
fails, take the service out of the way for the one-shot read:

  systemctl stop goodwe-guru
  cd /opt/goodwe-guru/backend && ../.venv/bin/python3 scratch_charge_limit_diag.py
  systemctl start goodwe-guru

Usage (on the CT):
  cd /opt/goodwe-guru/backend
  ../.venv/bin/python3 scratch_charge_limit_diag.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import settings as cfg  # noqa: E402

# Fields that bear on a charge-current derate, matched as substrings.
SUSPECT_TERMS = (
    "charge", "current", "curr", "_i", "temp", "soc", "limit", "lmt", "coff",
    "capacity", "cell", "volt", "bms", "batt", "dod", "power",
)


def _unwrap(v):
    return v.value if hasattr(v, "value") else v


def _units(inverter) -> dict:
    """Map sensor/setting id -> unit, so the dump is readable. Best-effort."""
    units = {}
    for getter in ("sensors", "settings"):
        try:
            for s in getattr(inverter, getter)():
                uid = getattr(s, "id_", None) or getattr(s, "id", None)
                if uid:
                    units[str(uid)] = getattr(s, "unit", "") or ""
        except Exception:
            pass
    return units


def _dump(title: str, data: dict, units: dict) -> None:
    print(f"\n{'=' * 72}\n{title}  ({len(data)} fields)\n{'=' * 72}")
    for k in sorted(data, key=str):
        u = units.get(str(k), "")
        print(f"  {str(k):<34} {str(_unwrap(data[k])):>18} {u}")


def _suspects(settings: dict, runtime: dict, units: dict) -> None:
    print(f"\n{'=' * 72}\nSUSPECTS — fields related to charge current / temp / limits\n{'=' * 72}")
    seen = set()
    for src, data in (("setting", settings), ("runtime", runtime)):
        for k in sorted(data, key=str):
            key = str(k)
            low = key.lower()
            if not any(t in low for t in SUSPECT_TERMS):
                continue
            if (src, key) in seen:
                continue
            seen.add((src, key))
            print(f"  [{src:<7}] {key:<32} {str(_unwrap(data[k])):>18} {units.get(key, '')}")


def _verdict(settings: dict, runtime: dict) -> None:
    """Point at the most likely cause from the numbers we just read."""
    g = lambda d, *keys: next((_unwrap(d[k]) for k in keys if k in d), None)  # noqa: E731

    charge_i = g(settings, "charge_i")
    charge_v = g(settings, "charge_v")
    soc      = g(runtime, "battery_soc", "bms_bat_soc")
    tmax     = g(runtime, "battery_max_cell_temp", "battery_temperature", "bms_bat_temperature")
    tmin     = g(runtime, "battery_min_cell_temp")
    vmax     = g(runtime, "battery_max_cell_voltage")
    vmin     = g(runtime, "battery_min_cell_voltage")
    pbat     = g(runtime, "pbattery1")
    capacity = g(settings, "battery_capacity", "battery_modules")

    print(f"\n{'=' * 72}\nREAD-OFF\n{'=' * 72}")
    if charge_i is not None and charge_v is not None:
        try:
            amps = float(charge_i) / 10.0
            print(f"  charge_i {charge_i} raw = {amps:.1f} A -> ceiling "
                  f"~{amps * float(charge_v):.0f} W at {float(charge_v):.1f} V")
        except Exception:
            pass
    print(f"  SoC ................. {soc}")
    print(f"  cell temp min/max ... {tmin} / {tmax}   (LFP derates below ~5 C, above ~45 C)")
    print(f"  cell volt min/max ... {vmin} / {vmax}")
    if vmax is not None and vmin is not None:
        try:
            print(f"  cell delta .......... {float(vmax) - float(vmin):.3f} V   "
                  f"(>0.1 V suggests imbalance-driven taper)")
        except Exception:
            pass
    print(f"  battery power now ... {pbat} W")
    print(f"  configured capacity . {capacity}   (too low = current limit sized for less pack)")
    print("\n  If temps and cell delta are both healthy and capacity is right, the")
    print("  remaining suspect is the cloud-pushed BatBMSCurrLmtCoff (80) — check")
    print("  it in SolarGo; it is not written by this app.")


async def _connect(attempts: int = 3, pause: int = 20):
    """Connect, tolerating the contention/sulk described in the docstring.

    Long pause between attempts on purpose: goodwe.connect() already retries
    internally, and hammering the ES WiFi module is what crashes it.
    """
    import goodwe
    for n in range(1, attempts + 1):
        try:
            print(f"Connecting to inverter at {cfg.inverter_host} "
                  f"(attempt {n}/{attempts}) ...")
            return await goodwe.connect(cfg.inverter_host)
        except Exception as e:
            print(f"  failed: {type(e).__name__}: {str(e).splitlines()[0]}")
            if n == attempts:
                print("\nGiving up. The inverter is not answering at all — most likely the\n"
                      "running service has the stack busy, or the WiFi module needs a few\n"
                      "minutes after recent writes. Check the dashboard: if the app is\n"
                      "offline too, wait (or power-cycle) rather than retrying in a loop.\n"
                      "Otherwise stop the service for this one read:\n"
                      "  systemctl stop goodwe-guru && "
                      "../.venv/bin/python3 scratch_charge_limit_diag.py; "
                      "systemctl start goodwe-guru")
                return None
            print(f"  waiting {pause}s before retrying (never hammer this stack) ...")
            await asyncio.sleep(pause)


async def main():
    inverter = await _connect()
    if inverter is None:
        return
    print(f"Connected: {inverter.model_name} {inverter.serial_number} "
          f"({inverter.__class__.__name__})")

    units = _units(inverter)
    settings, runtime = {}, {}
    try:
        settings = await inverter.read_settings_data()
    except Exception as e:
        print(f"\nread_settings_data failed: {e}")
    try:
        runtime = await inverter.read_runtime_data()
    except Exception as e:
        print(f"\nread_runtime_data failed: {e}")

    _dump("SETTINGS", settings, units)
    _dump("RUNTIME", runtime, units)
    _suspects(settings, runtime, units)
    _verdict(settings, runtime)
    print("\nRead-only. Nothing was written.")


if __name__ == "__main__":
    asyncio.run(main())
