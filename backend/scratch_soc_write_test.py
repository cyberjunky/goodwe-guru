"""
SUPERVISED write test for the ES "Battery Forced Charge" registers found in
the decompiled SolarGo app (see scratch_soc_test.py for the read-only probe
and full background). This script WRITES to the inverter — read the warnings
below before running.

    register 0xB9B9 (47545) = Force Charge switch   (0=off, 1=on)
    register 0xB9BA (47546) = Battery SoC Upper Limit (10-100)

WHAT THIS TEST DOES ("test" mode):
  1. Reads current switch + SoC-limit values (baseline).
  2. Reads current live battery SoC.
  3. Sets the SoC Upper Limit to (current live SoC + 3), a SMALL increment —
     not a big jump — specifically so that if this register turns out to
     mean "actively force-charge the battery (possibly from grid) up to this
     value" rather than "passively cap charging at this value", the damage
     is bounded to a few percent, not a runaway charge to 100%.
  4. Enables the Force Charge switch.
  5. Reads both registers back to CONFIRM the write actually stuck (unlike
     the charge_i write from 2026-07-10, which "succeeded" but had zero
     real effect).
  6. Does NOT loop or monitor further — you watch the dashboard yourself.

WHAT TO WATCH FOR AFTERWARDS (only meaningful while solar is producing):
  - Battery power drops to ~0 W and SoC stops climbing once it reaches the
    new limit -> the mechanism WORKS as a charge cap.
  - Battery keeps charging past the new limit -> same as every previous
    attempt: register accepted, no real effect. Run revert mode immediately.
  - Anything that looks like AGGRESSIVE new charging behavior (e.g. sudden
    large grid import into the battery right after enabling) -> "Force
    Charge" may mean an active boost-charge command, not a passive cap.
    Run revert mode immediately.

REVERT ("revert" mode): disables the Force Charge switch (writes 0) and
reads back to confirm. This is the safe/off state observed before this
test (switch was 0 when first read).

Usage (on the CT, from /opt/goodwe-guru):
  .venv/bin/python3 backend/scratch_soc_write_test.py test
  .venv/bin/python3 backend/scratch_soc_write_test.py revert
"""

import asyncio
import sys

sys.path.insert(0, ".")
from config import settings as cfg

FORCE_CHARGE_SWITCH_REG = 0xB9B9  # 47545
SOC_UPPER_LIMIT_REG     = 0xB9BA  # 47546


async def read_registers(inverter):
    cmd = inverter._read_command(FORCE_CHARGE_SWITCH_REG, 2)
    resp = await inverter._read_from_socket(cmd)
    resp.seek(FORCE_CHARGE_SWITCH_REG)
    switch = int.from_bytes(resp.read(2), "big")
    soc_limit = int.from_bytes(resp.read(2), "big")
    return switch, soc_limit


async def write_register(inverter, register: int, value: int):
    cmd = inverter._write_command(register, value)
    await inverter._read_from_socket(cmd)


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("test", "revert"):
        print(__doc__)
        return

    import goodwe
    print(f"Connecting to inverter at {cfg.inverter_host} ...")
    inverter = await goodwe.connect(cfg.inverter_host)
    print(f"Connected: {inverter.model_name} {inverter.serial_number}\n")

    switch, soc_limit = await read_registers(inverter)
    print(f"BEFORE: Force Charge switch = {switch}, SoC Upper Limit = {soc_limit}")

    if mode == "revert":
        print("\nReverting: writing Force Charge switch = 0 ...")
        await write_register(inverter, FORCE_CHARGE_SWITCH_REG, 0)
        await asyncio.sleep(2)
        switch2, soc_limit2 = await read_registers(inverter)
        print(f"AFTER:  Force Charge switch = {switch2}, SoC Upper Limit = {soc_limit2}")
        if switch2 == 0:
            print("\nConfirmed OFF. Safe.")
        else:
            print("\nWARNING: switch did not read back as 0 — inspect manually.")
        return

    # mode == "test"
    raw = await inverter.read_runtime_data()
    live_soc = raw.get("battery_soc")
    live_soc = int(getattr(live_soc, "value", live_soc)) if live_soc is not None else None
    if live_soc is None:
        print("Could not read live battery_soc — aborting, nothing written.")
        return
    target = min(live_soc + 3, 100)
    print(f"Live battery SoC right now: {live_soc}%")
    print(f"Setting SoC Upper Limit -> {target}% (small +3 increment, bounded risk)")

    await write_register(inverter, SOC_UPPER_LIMIT_REG, target)
    await asyncio.sleep(2)
    print("Enabling Force Charge switch ...")
    await write_register(inverter, FORCE_CHARGE_SWITCH_REG, 1)
    await asyncio.sleep(2)

    switch2, soc_limit2 = await read_registers(inverter)
    print(f"\nAFTER: Force Charge switch = {switch2}, SoC Upper Limit = {soc_limit2}")
    if switch2 == 1 and soc_limit2 == target:
        print("\nWrite CONFIRMED (register readback matches). Now watch the dashboard:")
        print(f"  - live SoC is {live_soc}%, target is {target}%")
        print("  - does charging stop at the target, or keep going?")
        print("  - does anything look like aggressive forced grid charging?")
        print("\nWhen done observing, run:  .venv/bin/python3 backend/scratch_soc_write_test.py revert")
    else:
        print("\nWrite did NOT read back as expected — do not trust this register. Reverting now.")
        await write_register(inverter, FORCE_CHARGE_SWITCH_REG, 0)


if __name__ == "__main__":
    asyncio.run(main())
