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

Capping battery CHARGE was long believed impossible on this ES — the
ECO_CHARGE eco_mode_soc target (ignored by EcoModeV1 firmware), an eco
0%-power "park" schedule (stops grid charging, not PV-surplus charging),
and a generic write_setting("charge_i", 0) register write (silently
ignored) all failed. What DOES work (confirmed live 2026-07-30 on the
owner's GW3648D-ES) is the dedicated AA55 command 0x0351 "set battery
charge parameters", reverse-engineered from the SolarGo app — same
precedent as set_ongrid_battery_dod (0x0560): the dedicated command
works, the generic register write doesn't. Routed below as "charge_i"
(0 = brake, stops ALL charging incl. PV surplus). Read-back of the
register may differ from the written value (BMS-negotiated, drifts on
its own) — that's normal; a clean command ack means success. See
CLAUDE.md 2026-07-30.
"""

import asyncio
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

# Serializes ALL inverter I/O across the app (main poll loop, battery
# scheduler, automations, Telegram bot, Settings API, reconnects). The AA55/
# UDP stack on the ES is a small embedded implementation with no real
# concurrent-session handling — two coroutines hitting it at once (e.g. the
# battery scheduler's write+verify-read cycle overlapping the main poll's
# read_runtime_data(), both plausible within the same ~20s window) can each
# get a garbled/missing response. Every direct inverter call in the backend
# MUST be wrapped in `async with inverter_lock:` — apply_setting() below does
# this for all writes; main.py wraps its own direct reads the same way.
inverter_lock = asyncio.Lock()


async def apply_setting(inverter, key: str, value, source: str = "",
                        notify: bool = True) -> str:
    """Write one setting via the best available method. Returns a short description.

    This is the single choke point for every command the app sends to the
    inverter, so it is also where the Telegram "setting sent" notification is
    raised — the Settings page, automations, the battery scheduler and the
    Telegram bot are all covered without each having to remember. `source`
    labels who asked, and is only used for that message.

    The notification is dispatched as a background task: Telegram is a network
    call that can be slow or down, and it must never delay a write that is
    holding `inverter_lock`, nor turn a successful write into a failure.

    `notify=False` is for callers that write in a verify/retry loop and report
    the final outcome themselves, so one logical change stays one message.
    """
    if inverter is None:
        raise RuntimeError("Inverter not connected")

    try:
        async with inverter_lock:
            desc = await _apply_setting_locked(inverter, key, value)
    except Exception as e:
        if notify:
            _notify_write(key, value, "", source, error=f"{type(e).__name__}: {e}")
        raise
    if notify:
        _notify_write(key, value, desc, source)
    return desc


def _notify_write(key: str, value, desc: str, source: str, error: str = "") -> None:
    try:
        from notifications import notify_inverter_write
        asyncio.get_running_loop().create_task(
            notify_inverter_write(key, value, desc, source, error))
    except Exception as e:      # no loop, import problem — never break the write
        log.debug("write notification skipped: %s", e)


async def _apply_setting_locked(inverter, key: str, value) -> str:
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

    if key == "dod":
        await inverter.set_ongrid_battery_dod(int(value))
        return f"on-grid DoD → {int(value)}%"

    if key == "charge_i":
        # Dedicated AA55 0x0351 "set battery charge parameters" — the only
        # write path that actually works on ES (see module docstring). Value
        # is raw, in 0.1 A units (186 = 18.6 A); 0 brakes all charging.
        # The command also carries the charge voltage, so re-send the
        # voltage just read, unchanged.
        from goodwe.protocol import Aa55ProtocolCommand
        current_raw = max(0, int(value))
        s = await inverter.read_settings_data()
        v = s.get("charge_v")
        v_raw = int(round(float(getattr(v, "value", v)) * 10))
        await inverter._read_from_socket(
            Aa55ProtocolCommand(f"035104{v_raw:04x}{current_raw:04x}", "03d1"))
        return f"charge current → {current_raw / 10:.1f} A"

    if key == "grid_export_limit":
        await inverter.set_grid_export_limit(int(value))
        return f"grid export limit → {int(value)} W"

    # Everything else still goes through the generic register write.
    await inverter.write_setting(key, value)
    return f"{key} → {value}"
