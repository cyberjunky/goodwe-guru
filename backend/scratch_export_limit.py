"""
Standalone read/write tool for the ES grid export limit, which is what makes
zero export work. This is the manual version of the scheduler's `zero_export`
flag (Battery page). It is for checking what the inverter really holds, or for
setting it by hand without the app.

HOW EXPORT LIMITING WORKS ON THE ES (confirmed live on GW3648D-ES, 2026-10-29):
  grid_export_limit  settings offset 52, W. Written with the dedicated AA55
                     command 0x0335 (the library's set_grid_export_limit):
                         request  AA55 C07F 0335 02 <W:2> <checksum:2>
                         response ... 03B5 ...
  grid_export        settings offset 18. This is the limit ON/OFF switch, and
                     1 = limit enforced. The library has no writer for it.
                     SolarGo uses AA55 0x0353 "BackflowPrevention"
                     (decompiled DataCollectUtil.buildPackage case 57):
                         request  AA55 C07F 0353 01 <01|00> <checksum:2>
                         response ... 03D3 ...
The value that was "missing" was the limit: it sat at 6000 W, above the 3.6 kW
rating, so it never applied. The switch was already 1. With the limit at 0 W,
ES General mode serves the house from solar first and then the battery. Any
surplus beyond that is curtailed instead of exported.

Usage (on the CT):
  cd /opt/goodwe-guru/backend
  ../.venv/bin/python3 scratch_export_limit.py read          # no writes
  ../.venv/bin/python3 scratch_export_limit.py set 0         # zero export
  ../.venv/bin/python3 scratch_export_limit.py set 6000      # revert (old value)
  ../.venv/bin/python3 scratch_export_limit.py enforce on    # switch, 0x0353
  ../.venv/bin/python3 scratch_export_limit.py enforce off

This script talks to the inverter directly, so it does NOT go through the app's
inverter_lock. If the service is polling at the same moment, a read may time
out. The fragile AA55/WiFi stack also sometimes stops answering for up to about
a minute after a write. In either case wait and run `read` again; do not retry
in a loop. Expect a short timeout, not damage. Each `set`/`enforce` call does
ONE write (the inverter's flash has limited write endurance).

The result only shows while there is a solar surplus. In that case `read`
should show pgrid ~0 (not exporting), the battery charging, and ppv dropping
below what the panels could make (curtailment).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import settings as cfg  # noqa: E402


async def read_state(inverter):
    s = await inverter.read_settings_data()
    val = lambda k: getattr(s.get(k), "value", s.get(k))  # noqa: E731
    print(f"grid_export_limit = {val('grid_export_limit')} W, "
          f"grid_export (enforced) = {val('grid_export')}")
    await asyncio.sleep(3)
    r = await inverter.read_runtime_data()
    print(f"ppv={r.get('ppv')} W  pgrid={r.get('pgrid')} W ({r.get('grid_in_out_label')})  "
          f"battery={r.get('pbattery1')} W  soc={r.get('battery_soc')}%  "
          f"house={r.get('house_consumption')} W")


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if (mode not in ("read", "set", "enforce")
            or (mode == "set" and len(sys.argv) < 3)
            or (mode == "enforce" and sys.argv[2:3] not in (["on"], ["off"]))):
        print(__doc__)
        return

    import goodwe
    print(f"Connecting to inverter at {cfg.inverter_host} ...")
    inverter = await goodwe.connect(cfg.inverter_host)
    print(f"Connected: {inverter.model_name} {inverter.serial_number}\n")

    if mode == "set":
        watts = int(sys.argv[2])
        print(f"Writing 0x0335: grid_export_limit = {watts} W ...")
        await inverter.set_grid_export_limit(watts)
        await asyncio.sleep(5)
    elif mode == "enforce":
        from goodwe.protocol import Aa55ProtocolCommand
        on = 1 if sys.argv[2] == "on" else 0
        print(f"Writing 0x0353: export limit {'ON' if on else 'OFF'} ...")
        await inverter._read_from_socket(Aa55ProtocolCommand(f"035301{on:02x}", "03d3"))
        await asyncio.sleep(5)

    await read_state(inverter)


if __name__ == "__main__":
    asyncio.run(main())
