"""
Forecast-driven battery hold + SoC charge cap.

Hold: the battery is HELD (DoD = day_dod, default 0 → no discharge, grid
covers the house) while the *solar forecast* for the current hour is at or
above `threshold_kwh` AND SoC is below `max_soc`, and released (DoD =
night_dod, default 80) once either condition fails. This tracks actual
production windows far better than civil sunrise/sunset.

Charge cap: `charge_cap_soc` (0 = disabled) brakes ALL battery charging
(charge current → 0 via the dedicated AA55 0x0351 command, confirmed working
on this ES 2026-07-30 — see inverter_io.py) once SoC reaches it, and
releases (restores `charge_cap_release_current`) at `charge_cap_soc` - 5.
Surplus PV exports while braked. ≤2 writes/day, flash-safe.

BMS maintenance: LFP SoC estimation drifts without periodic full charges, so
every `charge_cap_full_every_days` days (0 = never) the cap is skipped for a
day, letting the battery reach 100% for BMS top-balancing/recalibration.

The scheduler (in main.lifespan) re-checks every 5 min against cached
forecast and live poll data — no extra inverter queries for triggering; the
only inverter writes happen on a state transition.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

from config import settings as cfg

log = logging.getLogger(__name__)

_FILE = Path(cfg.db_path).parent / "battery_schedule.json"


@dataclass
class BatterySchedule:
    enabled:       bool  = False
    threshold_kwh: float = 0.1   # hold while this hour's solar forecast ≥ this (kWh)
    day_dod:       int   = 0     # DoD while held (0 = hold, no discharge)
    night_dod:     int   = 80    # DoD once released (normal discharge to 20%)
    max_soc:       int   = 100   # release the discharge hold once SoC reaches this
                                 # (100 = hold all day). A release threshold, not a
                                 # cap — the cap below is what stops charging.
    charge_cap_soc:              int   = 0    # brake ALL charging at this SoC (0 = off)
    charge_cap_release_current:  int   = 186  # raw 0.1 A units written on release
                                              # (186 = 18.6 A, the value found
                                              # commissioned on 2026-07-30)
    charge_cap_full_every_days:  int   = 14   # skip the cap this often for a 100%
                                              # BMS-balancing charge (0 = never)
    # ── scheduler state (persisted, not user settings) ──
    charge_cap_active:     bool = False  # True while the brake is applied
    charge_cap_last_full:  str  = ""     # ISO date of last completed 100% charge


def load_schedule() -> BatterySchedule:
    if _FILE.exists():
        try:
            d = json.loads(_FILE.read_text())
            s = BatterySchedule()
            for k, v in d.items():
                if hasattr(s, k):
                    setattr(s, k, v)
            return s
        except Exception:
            pass
    return BatterySchedule()


def save_schedule(s: BatterySchedule) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(asdict(s), indent=2))
