"""
Centralised inverter writes.

The goodwe library exposes dedicated methods that perform the correct command
sequence for certain settings. Writing those via the generic `write_setting()`
silently fails on the ES (the register write "succeeds" but the inverter ignores
it — confirmed for work_mode, and `dod` isn't even writable that way). Route them
through the proper methods here so there is ONE place that knows the mapping.

Confirmed on ES (platform 105):
  - set_operation_mode(OperationMode)      work mode actually sticks
  - set_ongrid_battery_dod(dod)            depth-of-discharge floor (0 = no discharge)
  - set_grid_export_limit(watts)
"""

import logging

from goodwe import OperationMode

log = logging.getLogger(__name__)

_OPMODE = {
    0: OperationMode.GENERAL,
    1: OperationMode.OFF_GRID,
    2: OperationMode.BACKUP,
    3: OperationMode.ECO,
    4: OperationMode.PEAK_SHAVING,
    5: OperationMode.SELF_USE,
}


async def apply_setting(inverter, key: str, value) -> str:
    """Write one setting via the best available method. Returns a short description."""
    if inverter is None:
        raise RuntimeError("Inverter not connected")

    if key == "work_mode":
        mode = _OPMODE.get(int(value))
        if mode is None:
            raise ValueError(f"Unknown work mode {value}")
        await inverter.set_operation_mode(mode)
        return f"operation mode → {mode.name}"

    if key == "eco_charge":
        # Eco mode, all-day GRID charge schedule. CAUTION: the SoC target is only
        # honoured on EcoModeV2 firmware (ES ARM fw ≥ 14). EcoModeV1 has no SoC
        # field in the schedule — encode_charge() silently drops it and the
        # inverter charges from the grid with NO cap. Callers MUST pair this
        # with a stop rule (automation or the scheduler's charge cap).
        soc = max(10, min(int(value), 100))
        await inverter.set_operation_mode(OperationMode.ECO_CHARGE,
                                          eco_mode_power=100, eco_mode_soc=soc)
        return f"ECO charge → target {soc}%"

    if key == "eco_discharge":
        power = max(0, min(int(value), 100))
        await inverter.set_operation_mode(OperationMode.ECO_DISCHARGE, eco_mode_power=power)
        return f"ECO discharge → {power}% power"

    if key == "charge_current":
        # BMS-level battery charge current limit (A). 0 = no charging at all —
        # the only reliable way to stop PV-surplus charging on this ES (the
        # eco 0%-power park stopped grid charging but NOT solar charging, and
        # EcoModeV1 ignores SoC targets). The scheduler's charge cap sets 0 at
        # the cap and restores the configured normal current on release.
        await inverter.write_setting("charge_i", int(value))
        return f"battery charge current → {int(value)} A"

    if key == "dod":
        await inverter.set_ongrid_battery_dod(int(value))
        return f"on-grid DoD → {int(value)}%"

    if key == "grid_export_limit":
        await inverter.set_grid_export_limit(int(value))
        return f"grid export limit → {int(value)} W"

    # Everything else still goes through the generic register write.
    await inverter.write_setting(key, value)
    return f"{key} → {value}"
