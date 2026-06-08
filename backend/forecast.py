"""
Solar production forecast via Forecast.Solar (free tier, no API key for basic use).

API docs: https://doc.forecast.solar/api:estimate
Endpoint: GET https://api.forecast.solar/estimate/{lat}/{lon}/{dec}/{az}/{kwp}

  lat   – latitude  (decimal degrees, e.g. 52.37)
  lon   – longitude (decimal degrees, e.g. 4.90)
  dec   – panel tilt / declination  (degrees, 0=flat, 90=vertical, typical 30-45)
  az    – azimuth   (degrees, 0=south, -90=east, 90=west)
  kwp   – installed peak power (kW, e.g. 6.5)

Multiple plane support: call multiple times and sum — or use the paid /estimate/info endpoint.
Response is cached for 30 minutes to respect rate limits.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx

from config import settings as cfg

log = logging.getLogger(__name__)

_CACHE_FILE = Path(cfg.db_path).parent / "forecast_cache.json"
_CONFIG_FILE = Path(cfg.db_path).parent / "forecast_config.json"
_CACHE_TTL  = 1800   # 30 minutes


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ForecastPlane:
    """One roof plane / string orientation."""
    label:    str   = "Main"
    kwp:      float = 4.0
    tilt:     int   = 35    # degrees (0=flat)
    azimuth:  int   = 0     # 0=south, -90=east, 90=west


@dataclass
class ForecastConfig:
    enabled:  bool              = False
    lat:      float             = 52.37
    lon:      float             = 4.90
    planes:   list              = field(default_factory=lambda: [asdict(ForecastPlane())])
    horizon:  str               = ""    # optional horizon string for shading


def load_forecast_config() -> ForecastConfig:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text())
            fc = ForecastConfig()
            for k, v in data.items():
                if hasattr(fc, k):
                    setattr(fc, k, v)
            return fc
        except Exception:
            pass
    return ForecastConfig()


def save_forecast_config(fc: ForecastConfig):
    # Normalise numeric fields (frontend may send comma decimals or strings)
    fc.lat = _num(fc.lat)
    fc.lon = _num(fc.lon)
    for p in fc.planes:
        if isinstance(p, dict):
            p["kwp"]     = _num(p.get("kwp", 0))
            p["tilt"]    = int(_num(p.get("tilt", 0)))
            p["azimuth"] = int(_num(p.get("azimuth", 0)))
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(asdict(fc), indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + cache
# ─────────────────────────────────────────────────────────────────────────────
_mem_cache: dict[str, Any] = {}


def _num(x: Any) -> float:
    """Coerce to float, tolerating European decimal commas (e.g. '51,813297')."""
    try:
        return float(str(x).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


async def fetch_forecast(fc: ForecastConfig) -> dict:
    """
    Returns merged forecast across all configured planes.

    Shape:
      {
        "watt_hours_day": {"2024-06-08": 18500.0, "2024-06-09": 21000.0},
        "watts":          {"2024-06-08 07:00:00": 0, "2024-06-08 08:00:00": 312, ...},
        "fetched_at":     1717840000,
        "planes":         [{"label": "Main", "kwp": 4.0, ...}],
      }
    """
    if not fc.enabled or not fc.planes:
        return {}

    # Check memory cache
    cache_key = f"{fc.lat}:{fc.lon}:{json.dumps(fc.planes)}"
    cached = _mem_cache.get(cache_key)
    if cached and time.time() - cached["fetched_at"] < _CACHE_TTL:
        return cached

    # Check disk cache (survives restarts within TTL)
    if _CACHE_FILE.exists():
        try:
            disk = json.loads(_CACHE_FILE.read_text())
            if time.time() - disk.get("fetched_at", 0) < _CACHE_TTL:
                _mem_cache[cache_key] = disk
                return disk
        except Exception:
            pass

    merged_watts:     dict[str, float] = {}
    merged_wh_day:    dict[str, float] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        for plane in fc.planes:
            url = (
                f"https://api.forecast.solar/estimate"
                f"/{_num(fc.lat)}/{_num(fc.lon)}"
                f"/{int(_num(plane['tilt']))}/{int(_num(plane['azimuth']))}"
                f"/{_num(plane['kwp'])}"
            )
            try:
                r = await client.get(url)
                if r.status_code >= 400:
                    log.warning("Forecast.Solar %s → %s: %s", url, r.status_code, r.text[:200])
                r.raise_for_status()
                data = r.json()
                result = data.get("result", {})
                for ts, w in result.get("watts", {}).items():
                    merged_watts[ts] = merged_watts.get(ts, 0) + w
                for date, wh in result.get("watt_hours_day", {}).items():
                    merged_wh_day[date] = merged_wh_day.get(date, 0) + wh
            except Exception as e:
                log.warning("Forecast.Solar fetch failed for plane %s: %s", plane.get("label"), e)

    result = {
        "watts":          merged_watts,
        "watt_hours_day": merged_wh_day,
        "fetched_at":     int(time.time()),
        "planes":         fc.planes,
    }
    _mem_cache[cache_key] = result
    try:
        _CACHE_FILE.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def hourly_today(forecast: dict) -> list[dict]:
    """
    Return list of {hour: int, watts: float} for today,
    suitable for charting.
    """
    from datetime import date
    today = date.today().isoformat()
    rows = []
    for ts, w in sorted(forecast.get("watts", {}).items()):
        if ts.startswith(today):
            hour = int(ts[11:13])
            rows.append({"hour": hour, "watts": w})
    return rows


def daily_forecast(forecast: dict) -> list[dict]:
    """Return list of {date: str, kwh: float} for the next few days."""
    rows = []
    for date_str, wh in sorted(forecast.get("watt_hours_day", {}).items()):
        rows.append({"date": date_str, "kwh": round(wh / 1000, 2)})
    return rows
