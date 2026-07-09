"""
Forecast-driven battery hold.

When enabled, the battery is HELD (DoD = day_dod, default 0 → no discharge, grid
covers the house) while the *solar forecast* for the current hour is at or above
`threshold_kwh`, and allowed to discharge (DoD = night_dod, default 80) when it
drops below. This tracks actual production windows far better than civil
sunrise/sunset (panels don't meaningfully produce right at sunrise/sunset).

The scheduler (in main.lifespan) re-checks every 5 min against the cached
forecast and writes DoD only on a hold↔release transition — ~2 writes/day,
flash-safe.
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
    day_dod:       int   = 0     # DoD while producing (0 = hold, no discharge)
    night_dod:     int   = 80    # DoD when below threshold (normal discharge to 20%)
    max_soc:       int   = 80    # charge cap (%): stop charging here (100 = no cap).
                                 # Enforced by setting the battery charge current to 0 A
                                 # at the cap (nothing else stops PV charging on this ES).
    charge_a:      int   = 40    # normal charge current (A) restored when the cap releases


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
