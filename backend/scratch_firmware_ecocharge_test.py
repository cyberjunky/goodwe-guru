"""
SUPERVISED test: does the goodwe library's STANDARD ECO_CHARGE call now cap
charging, after GoodWe pushed a firmware update? Unlike scratch_eco_bypass_test.py
(which hand-builds the raw bytes), this uses ONLY the official library method —
if the firmware fix is real, this is the path that should now work correctly
for everyone using the goodwe library, not just a hand-crafted workaround.

    await inverter.set_operation_mode(
        OperationMode.ECO_CHARGE, eco_mode_power=100, eco_mode_soc=target)

Same safety discipline as every previous test this week: small bounded
increment above LIVE SoC (not a big jump), read-back verification, explicit
revert path. Prints ARM/DSP firmware first so you can confirm the update
actually landed before drawing any conclusion from the behavior test.

Usage (on the CT, from /opt/goodwe-guru):
  .venv/bin/python3 backend/scratch_firmware_ecocharge_test.py test
  .venv/bin/python3 backend/scratch_firmware_ecocharge_test.py revert
"""

import asyncio
import sys

sys.path.insert(0, ".")
from config import settings as cfg

ECO_GROUP_0_REGISTER = 0xB9BB  # 47547


async def read_schedule(inverter) -> bytes:
    cmd = inverter._read_command(ECO_GROUP_0_REGISTER, 6)
    resp = await inverter._read_from_socket(cmd)
    resp.seek(ECO_GROUP_0_REGISTER)
    return resp.read(12)


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("test", "revert"):
        print(__doc__)
        return

    import goodwe
    from goodwe import OperationMode
    from inverter_io import apply_setting

    print(f"Connecting to inverter at {cfg.inverter_host} ...")
    inverter = await goodwe.connect(cfg.inverter_host)
    print(f"Connected: {inverter.model_name} {inverter.serial_number}")
    print(f"  ARM firmware: {getattr(inverter, 'arm_version', '?')}  "
          f"DSP firmware: {getattr(inverter, 'dsp1_version', '?')}  "
          f"raw firmware string: {getattr(inverter, 'firmware', '?')}")
    print(f"  EcoModeV2 supported (per library's own check): "
          f"{inverter._supports_eco_mode_v2() if hasattr(inverter, '_supports_eco_mode_v2') else '?'}\n")

    before = await read_schedule(inverter)
    print(f"BEFORE eco group 0 raw bytes: {before.hex()}")

    if mode == "revert":
        print("\nReverting: setting work_mode -> General (0) ...")
        await apply_setting(inverter, "work_mode", 0)
        await asyncio.sleep(2)
        after = await read_schedule(inverter)
        print(f"AFTER  eco group 0 raw bytes: {after.hex()}")
        print("General mode should have cleared the schedule (all zeros expected).")
        return

    # mode == "test"
    target_soc = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if target_soc is None:
        raw = await inverter.read_runtime_data()
        live_soc = raw.get("battery_soc")
        live_soc = int(getattr(live_soc, "value", live_soc)) if live_soc is not None else 50
        target_soc = min(live_soc + 3, 100)
        print(f"No target given -- using live SoC + 3 = {target_soc}% (bounded risk)")

    print(f"\nCalling inverter.set_operation_mode(ECO_CHARGE, eco_mode_power=100, eco_mode_soc={target_soc}) "
          f"-- the STANDARD library method, no bypass ...")
    await inverter.set_operation_mode(OperationMode.ECO_CHARGE, eco_mode_power=100, eco_mode_soc=target_soc)
    await asyncio.sleep(2)

    after = await read_schedule(inverter)
    print(f"\nAFTER  eco group 0 raw bytes: {after.hex()}")
    print(f"  (byte 8-9 should encode SoC target {target_soc} = 0x{target_soc:04x} if the library wrote it there)")
    print(f"\nNow watch the dashboard: does charging stop at SoC {target_soc}%, or keep going?")
    print("When done observing, run: .venv/bin/python3 backend/scratch_firmware_ecocharge_test.py revert")


if __name__ == "__main__":
    asyncio.run(main())
