"""
Sunrise/sunset-driven battery hold.

When enabled, the battery is HELD (DoD = day_dod, default 0 → no discharge, grid
covers the house) during daylight, and allowed to discharge (DoD = night_dod,
default 80) at night. Sun times come from the `astral` package using the
forecast config's lat/lon — no network, no fixed clock times, tracks the seasons.

The scheduler (in main.lifespan) writes DoD only on a day↔night transition, so
it's ~2 inverter writes/day — flash-safe.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from config import settings as cfg

log = logging.getLogger(__name__)

_FILE = Path(cfg.db_path).parent / "battery_schedule.json"


@dataclass
class BatterySchedule:
    enabled:   bool = False
    day_dod:   int  = 0     # DoD while the sun is up (0 = hold, no discharge)
    night_dod: int  = 80    # DoD after sunset (normal discharge to 20%)


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


def sun_times(lat, lon):
    """Today's (sunrise, sunset) as tz-aware UTC datetimes."""
    from astral import LocationInfo
    from astral.sun import sun
    loc = LocationInfo(latitude=float(lat), longitude=float(lon))
    s = sun(loc.observer, date=datetime.now(timezone.utc).date(), tzinfo=timezone.utc)
    return s["sunrise"], s["sunset"]


def is_daytime(lat, lon) -> bool:
    sr, ss = sun_times(lat, lon)
    return sr <= datetime.now(timezone.utc) <= ss
