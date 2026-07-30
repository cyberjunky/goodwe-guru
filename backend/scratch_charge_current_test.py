"""
SUPERVISED test for the dedicated AA55 "set battery charge parameters"
command (0x0351) — the handbrake candidate for capping battery charging.

WHY THIS IS DIFFERENT FROM THE FAILED charge_i ATTEMPT (2026-07-09/10):
The earlier attempt used the library's generic `write_setting("charge_i", 0)`,
which sends an AA55 *register write* to the settings-read offset (26) — the
same class of silently-ignored write as work_mode/dod before their dedicated
commands were found. The official SolarGo app (com.goodwe.solargo v7.1.0,
DataCollectUtil.java case 105 + Constant.SET_BATTERY_CHARGE_PAREMETERS_REQUEST_CODE
= {0x03, 0x51}) does NOT use a register write for charge current. It uses a
dedicated AA55 protocol command:

    request:  AA55 C07F 0351 04 <chargeV:2> <chargeI:2> <checksum:2>
    response: ... 03D1 ...

This is the exact same precedent as `set_ongrid_battery_dod` (dedicated
command 0x0560) and `set_grid_export_limit` (dedicated command 0x0335),
both of which DO work on this ES while their generic register writes don't.
This command has NEVER been tested here.

PAYLOAD ENCODING (from the app):
  chargeV = volts x 10  (app default 575 = 57.5 V; matches library's
                         Decimal("charge_v", 24, 10) settings offset)
  chargeI = amps x 10   (app UI accepts 0-100 A, stores value x 10;
                         library reads offset 26 as a raw Integer, so we
                         reuse the raw value we read — apples to apples)
The command writes BOTH values together, so we always re-send the voltage
we just read and only change the current.

WHAT THIS TEST DOES:
  read    - print current charge_v / charge_i settings. No writes.
  test    - set charge current to 0 (handbrake), KEEPING the charge voltage
            unchanged. Read back to confirm the write stuck.
  revert  - restore the charge current saved by `test` (printed + written to
            scratch_charge_current_backup.json). Read back to confirm.

Usage (on the CT):
  cd /opt/goodwe-guru/backend
  ../.venv/bin/python3 scratch_charge_current_test.py read
  ../.venv/bin/python3 scratch_charge_current_test.py test
  ../.venv/bin/python3 scratch_charge_current_test.py revert

WHAT TO WATCH AFTER `test` (only meaningful while PV is producing surplus):
  - Battery power (pbattery1) drops to ~0 W and SoC stops climbing
    -> HAND BRAKE WORKS: we can cap SoC at 80% app-side with 2 writes/day.
  - Battery keeps charging as before -> another silently-accepted write;
    run `revert`, log the outcome, and close this line of investigation.
  - Any fault/alarm on the inverter -> run `revert` immediately.

SAFETY: charge current 0 is a normal user-settable value in the SolarGo app
(UI range 0-100 A), and the voltage is re-sent unchanged. Reversible via
`revert`. Running `test` twice in a row is safe: the backup of the original
current is never overwritten with 0.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import settings as cfg  # noqa: E402

BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "scratch_charge_current_backup.json")


async def read_params(inverter):
    """Returns (charge_v_raw, charge_i_raw) in wire units (V x10, raw current)."""
    settings = await inverter.read_settings_data()
    charge_v = settings.get("charge_v")
    charge_i = settings.get("charge_i")
    charge_v = float(getattr(charge_v, "value", charge_v))
    charge_i = int(getattr(charge_i, "value", charge_i))
    # charge_v is Decimal scale 10 (raw = V x 10); charge_i is a raw Integer.
    return int(round(charge_v * 10)), charge_i


async def send_charge_params(inverter, charge_v_raw: int, charge_i_raw: int):
    from goodwe.protocol import Aa55ProtocolCommand
    payload = f"035104{charge_v_raw:04x}{charge_i_raw:04x}"
    await inverter._read_from_socket(Aa55ProtocolCommand(payload, "03d1"))


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("read", "test", "revert"):
        print(__doc__)
        return

    import goodwe
    print(f"Connecting to inverter at {cfg.inverter_host} ...")
    inverter = await goodwe.connect(cfg.inverter_host)
    print(f"Connected: {inverter.model_name} {inverter.serial_number} "
          f"({inverter.__class__.__name__})\n")

    v_raw, i_raw = await read_params(inverter)
    print(f"NOW: charge_v raw = {v_raw} ({v_raw / 10:.1f} V), charge_i raw = {i_raw}")

    if mode == "read":
        print("\nRead-only. No changes made.")
        return

    if mode == "test":
        print(f"\nWriting 0x0351: charge_v = {v_raw} (unchanged), charge_i = 0 ...")
        await send_charge_params(inverter, v_raw, 0)
        await asyncio.sleep(2)
        v2, i2 = await read_params(inverter)
        print(f"AFTER: charge_v raw = {v2}, charge_i raw = {i2}")
        if i2 == 0 and v2 == v_raw:
            # Never save 0 as the "original" — that happens when test is run
            # twice in a row and would make revert restore 0 (observed live).
            if i_raw != 0:
                with open(BACKUP_FILE, "w") as f:
                    json.dump({"charge_v_raw": v_raw, "charge_i_raw": i_raw}, f)
                print(f"\nWrite CONFIRMED via read-back. Original charge_i ({i_raw}) "
                      f"saved to {os.path.basename(BACKUP_FILE)}.")
            elif os.path.exists(BACKUP_FILE):
                print(f"\nWrite CONFIRMED via read-back. Current was already 0; "
                      f"keeping the existing backup in {os.path.basename(BACKUP_FILE)}.")
            else:
                print("\nWrite CONFIRMED via read-back. NOTE: current was already 0 "
                      "and no backup exists — revert has nothing to restore; set the "
                      "desired current manually.")
            print("Watch the dashboard: does battery power drop to ~0 W and SoC stop climbing?")
            print("When done observing, run revert.")
        else:
            print("\nWrite did NOT read back as expected — values unchanged or "
                  "mismatched. Treat as silently ignored; nothing to revert.")

    elif mode == "revert":
        try:
            with open(BACKUP_FILE) as f:
                backup = json.load(f)
        except FileNotFoundError:
            print(f"No backup file ({BACKUP_FILE}) — nothing known to restore. Aborting.")
            return
        print(f"\nRestoring 0x0351: charge_v = {backup['charge_v_raw']}, "
              f"charge_i = {backup['charge_i_raw']} ...")
        await send_charge_params(inverter, backup["charge_v_raw"], backup["charge_i_raw"])
        await asyncio.sleep(2)
        v2, i2 = await read_params(inverter)
        print(f"AFTER: charge_v raw = {v2}, charge_i raw = {i2}")
        if i2 == backup["charge_i_raw"]:
            print("\nRestore CONFIRMED via read-back.")
            os.remove(BACKUP_FILE)
        else:
            print("\nWARNING: restore did not read back as expected — inspect manually.")


if __name__ == "__main__":
    asyncio.run(main())
