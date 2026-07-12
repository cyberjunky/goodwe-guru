"""
READ-ONLY diagnostic — do not write anything to the inverter.

Reverse-engineered from the official SolarGo Android app (com.goodwe.solargo,
v7.1.0) — see android/decompiled/sources/com/goodwe/hybrid/common/DataCollectUtil.java
and .../activity/BatteryFunction105Activity.java.

The app exposes a "Battery Forced Charge" feature (ES platform, ARM firmware
>= 14) with a genuine SoC-upper-limit target, using TWO ADJACENT holding
registers accessed via plain Modbus-RTU-style framing (comm addr 0xF7),
completely separate from the Eco Mode schedule mechanism this project has
already exhausted:

    register 0xB9B9 (47545, decimal) = Force Charge switch   (0=off, 1=on)
    register 0xB9BA (47546, decimal) = Battery SoC Upper Limit (10-100)

App's raw read frame:  F7 03 B9B9 0002 <crc_lo> <crc_hi>   (read 2 registers)
App's raw write frame: F7 06 B9B9 <value> <crc_lo> <crc_hi>  (write one register)
CRC is standard Modbus CRC16 (getUdpBytes() in the app == crc16, byte-swapped).

This script ONLY reads — it changes nothing on the inverter. Run it, paste
the full output back. If the two values printed look sane (switch is 0 or 1,
SoC value is a plausible 10-100 number), we've found a genuine, previously
untested mechanism and can move to a carefully-supervised write test next.
"""

import asyncio
import sys

sys.path.insert(0, ".")
from config import settings as cfg


async def main():
    import goodwe

    print(f"Connecting to inverter at {cfg.inverter_host} ...")
    inverter = await goodwe.connect(cfg.inverter_host)
    print(f"Connected: {inverter.model_name} {inverter.serial_number} "
          f"(platform={inverter.__class__.__name__}, arm_version={getattr(inverter, 'arm_version', '?')})")

    FORCE_CHARGE_SWITCH_REG = 0xB9B9  # 47545
    SOC_UPPER_LIMIT_REG     = 0xB9BA  # 47546

    print("\n--- Raw register read: 0xB9B9 (2 registers) ---")
    try:
        cmd = inverter._read_command(FORCE_CHARGE_SWITCH_REG, 2)
        resp = await inverter._read_from_socket(cmd)
        print("Response object:", resp)
        print("Response repr:", repr(resp))
        # Try every plausible accessor — we don't know which this library version exposes.
        for attr in ("response_data", "data", "raw_data", "raw", "value"):
            if hasattr(resp, attr):
                val = getattr(resp, attr)
                val = val() if callable(val) else val
                print(f"  .{attr} = {val!r}" + (f"  (hex: {val.hex()})" if isinstance(val, (bytes, bytearray)) else ""))
        if hasattr(resp, "seek") and hasattr(resp, "read"):
            try:
                resp.seek(FORCE_CHARGE_SWITCH_REG)
                switch_bytes = resp.read(2)
                soc_bytes = resp.read(2)
                print(f"  seek/read: switch_bytes={switch_bytes.hex()} soc_bytes={soc_bytes.hex()}")
                print(f"  => Force Charge switch = {int.from_bytes(switch_bytes, 'big')}")
                print(f"  => SoC Upper Limit     = {int.from_bytes(soc_bytes, 'big')}")
            except Exception as e:
                print(f"  seek/read failed: {e}")
    except Exception as e:
        print(f"READ FAILED: {type(e).__name__}: {e}")

    print("\n--- Also trying via read_settings_data() bulk read, in case these are already-mapped settings ---")
    try:
        all_settings = await inverter.read_settings_data()
        for k, v in all_settings.items():
            if "soc" in k.lower() or "force" in k.lower() or "charge" in k.lower():
                print(f"  {k} = {v}")
    except Exception as e:
        print(f"bulk settings read failed: {e}")

    print("\nDone. This script made NO changes to the inverter.")


if __name__ == "__main__":
    asyncio.run(main())
