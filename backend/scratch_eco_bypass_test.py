"""
SUPERVISED write test — bypasses the goodwe library's EcoModeV2 encoder
entirely and writes the eco-charge schedule using the EXACT byte layout
found in the decompiled SolarGo app (AddEcoModeActivity.saveData /
StringUtils.getSetModeAddress). See android/decompiled/... for the source.

WHY: every previous eco_charge test went through
`inverter.set_operation_mode(OperationMode.ECO_CHARGE, eco_mode_power=X,
eco_mode_soc=Y)`, which uses the LIBRARY's own byte encoder. The register
address it targets (0xB9BB / 47547) is confirmed IDENTICAL to what the real
app uses for eco group 0 -- so a wrong register isn't the problem. This
script sends the real app's byte layout directly, to rule the library's
encoder in or out as the actual bug, independent of firmware behaviour.

REGISTER: 0xB9BB (47547), eco schedule group 0, 6 registers / 12 bytes,
written via Modbus function 0x10 (write multiple registers):

    byte 0-1:  start time (hh, mm)              -> 00:00
    byte 2-3:  end time   (hh, mm)               -> 23:59
    byte 4:    0xFF (fixed marker seen in the app for group 0)
    byte 5:    repeat-day bitmask                 -> 0x7F (every day)
    byte 6-7:  power, int16, NEGATIVE for charge   -> -100 (0xFF9C)
    byte 8-9:  SoC target, uint16                  -> target_soc
    byte 10-11: 0x0000 (fixed, non-ET3 platforms)

This ALSO sets work_mode to ECO (same as the library's own
set_operation_mode(ECO_CHARGE, ...) does internally) -- required for any
eco schedule to take effect at all.

REVERT: switches back to General mode. The goodwe library's own code
confirms General mode explicitly clears eco schedules on this firmware
(_set_general_mode zeroes the charge/discharge time windows) -- this is
the SAME revert path already used and proven throughout this project.

Usage (on the CT, from /opt/goodwe-guru):
  .venv/bin/python3 backend/scratch_eco_bypass_test.py test [target_soc]
  .venv/bin/python3 backend/scratch_eco_bypass_test.py revert
"""

import asyncio
import struct
import sys

sys.path.insert(0, ".")
from config import settings as cfg

ECO_GROUP_0_REGISTER = 0xB9BB  # 47547 -- same register the goodwe library targets


def build_schedule_payload(target_soc: int, power_pct: int = 100) -> bytes:
    start_hh, start_mm = 0, 0
    end_hh, end_mm = 23, 59
    fixed_marker = 0xFF
    repeat_all_days = 0x7F
    power_i16 = (-abs(power_pct)) & 0xFFFF  # negative = charge, per the app
    return struct.pack(
        ">BBBBBBHHH",
        start_hh, start_mm, end_hh, end_mm, fixed_marker, repeat_all_days,
        power_i16, target_soc, 0,
    )


async def write_multi(inverter, register: int, payload: bytes):
    # Try the library's multi-register write primitive under a few plausible
    # names/import paths -- we don't know which this installed version has.
    import goodwe.protocol as proto

    for cls_name in ("ModbusRtuWriteMultiCommand", "ModbusWriteMultiCommand", "WriteMultiCommand"):
        cls = getattr(proto, cls_name, None)
        if cls is not None:
            print(f"Using {cls_name} for the multi-register write")
            cmd = cls(0xF7, register, payload)
            return await inverter._read_from_socket(cmd)

    raise RuntimeError(
        "No multi-register write command class found in goodwe.protocol. "
        f"Available names: {[n for n in dir(proto) if 'Command' in n]}"
    )


async def read_schedule(inverter) -> bytes:
    cmd = inverter._read_command(ECO_GROUP_0_REGISTER, 6)  # 6 registers = 12 bytes
    resp = await inverter._read_from_socket(cmd)
    resp.seek(ECO_GROUP_0_REGISTER)
    return resp.read(12)


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("test", "revert"):
        print(__doc__)
        return

    import goodwe
    print(f"Connecting to inverter at {cfg.inverter_host} ...")
    inverter = await goodwe.connect(cfg.inverter_host)
    print(f"Connected: {inverter.model_name} {inverter.serial_number} "
          f"(arm_version={getattr(inverter, 'arm_version', '?')})\n")

    before = await read_schedule(inverter)
    print(f"BEFORE eco group 0 raw bytes: {before.hex()}")

    if mode == "revert":
        print("\nReverting: setting work_mode -> General (0) ...")
        from inverter_io import apply_setting
        await apply_setting(inverter, "work_mode", 0)
        await asyncio.sleep(2)
        after = await read_schedule(inverter)
        print(f"AFTER  eco group 0 raw bytes: {after.hex()}")
        print("\nGeneral mode should have cleared the schedule (all zeros expected).")
        return

    # mode == "test"
    target_soc = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if target_soc is None:
        raw = await inverter.read_runtime_data()
        live_soc = raw.get("battery_soc")
        live_soc = int(getattr(live_soc, "value", live_soc)) if live_soc is not None else 50
        target_soc = min(live_soc + 3, 100)
        print(f"No target given -- using live SoC + 3 = {target_soc}% (bounded risk, same discipline as the force-charge test)")

    payload = build_schedule_payload(target_soc)
    print(f"Payload to write (hex): {payload.hex()}")
    print(f"  decoded: start=00:00 end=23:59 marker=0xFF repeat=0x7F "
          f"power=-100 soc={target_soc} trailer=0000")

    print("\nSetting work_mode -> ECO (3) ...")
    from inverter_io import apply_setting
    await apply_setting(inverter, "work_mode", 3)
    await asyncio.sleep(1)

    print("Writing eco schedule (bypassing the library's encoder) ...")
    await write_multi(inverter, ECO_GROUP_0_REGISTER, payload)
    await asyncio.sleep(2)

    after = await read_schedule(inverter)
    print(f"\nAFTER  eco group 0 raw bytes: {after.hex()}")
    if after == payload:
        print("Write CONFIRMED byte-for-byte. Now watch the dashboard:")
        print(f"  - does charging stop at SoC {target_soc}%, or keep going?")
        print("\nWhen done observing, run: .venv/bin/python3 backend/scratch_eco_bypass_test.py revert")
    else:
        print("Write did NOT read back as expected -- do not trust this. Reverting now.")
        await apply_setting(inverter, "work_mode", 0)


if __name__ == "__main__":
    asyncio.run(main())
