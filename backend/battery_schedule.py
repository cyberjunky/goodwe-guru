"""
Forecast-driven battery hold.

When enabled, the battery is HELD (DoD = day_dod, default 0 → no discharge, grid
covers the house) while the *solar forecast* for the current hour is at or above
`threshold_kwh` AND SoC is below `max_soc`, and released (DoD = night_dod,
default 80) once either condition fails. This tracks actual production windows
far better than civil sunrise/sunset (panels don't meaningfully produce right
at sunrise/sunset).

`max_soc` is NOT a charge cap — nothing in this ES's firmware can stop PV
surplus from charging the battery past it (see backend/inverter_io.py for
what was tried and confirmed non-functional). It only releases the discharge
hold once reached, so the battery isn't locked at a high SoC for hours with
no way down.

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
    day_dod:       int   = 0     # DoD while held (0 = hold, no discharge)
    night_dod:     int   = 80    # DoD once released (normal discharge to 20%)
    max_soc:       int   = 100   # release the hold once SoC reaches this (100 = never
                                 # release early — hold all day like before this field
                                 # existed). NOT a charge cap: nothing in this ES's
                                 # firmware can stop PV-surplus charging (see
                                 # inverter_io.py). This only stops the battery being
                                 # LOCKED at a high SoC with discharge blocked — once
                                 # reached, it's free to discharge again immediately.


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
