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


async def _fetch_openmeteo(fc: "ForecastConfig", client) -> tuple[dict, dict, list, str | None]:
    """
    Fallback forecast via Open-Meteo (free, no key, very reliable).
    Estimates PV from global horizontal irradiance: power(W) ≈ kWp · GHI · PR.
    Less precise than Forecast.Solar (ignores tilt/azimuth) but robust.
    """
    PR = 0.85
    watts: dict[str, float] = {}
    wh_day: dict[str, float] = {}
    errors: list[str] = []
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={_num(fc.lat)}&longitude={_num(fc.lon)}"
        f"&hourly=shortwave_radiation&forecast_days=3&timezone=auto"
    )
    tz: str | None = None
    try:
        r = await client.get(url)
        r.raise_for_status()
        body = r.json()
        tz = body.get("timezone")
        j = body.get("hourly", {})
        times = j.get("time", [])
        ghi   = j.get("shortwave_radiation", [])
        total_kwp = sum(_num(p.get("kwp", 0)) for p in fc.planes)
        for t, g in zip(times, ghi):
            if g is None:
                continue
            p = total_kwp * float(g) * PR        # W
            ts = t.replace("T", " ")
            watts[ts] = watts.get(ts, 0) + p
            wh_day[t[:10]] = wh_day.get(t[:10], 0) + p   # × 1 h
    except Exception as e:
        errors.append(f"Open-Meteo: {e}")
    return watts, wh_day, errors, tz


def clear_cache():
    """Drop cached forecast so the next fetch hits the API (e.g. after a config change)."""
    _mem_cache.clear()
    try:
        _CACHE_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


async def fetch_forecast(fc: ForecastConfig, force: bool = False) -> dict:
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

    # Cache is keyed on location + planes; honour it on disk too (a config change
    # must invalidate the cache, otherwise re-saving returns the stale result).
    cache_key = f"{_num(fc.lat)}:{_num(fc.lon)}:{json.dumps(fc.planes, sort_keys=True)}"
    if not force:
        cached = _mem_cache.get(cache_key)
        if cached and time.time() - cached["fetched_at"] < _CACHE_TTL:
            return cached
        if _CACHE_FILE.exists():
            try:
                disk = json.loads(_CACHE_FILE.read_text())
                if disk.get("cache_key") == cache_key and time.time() - disk.get("fetched_at", 0) < _CACHE_TTL:
                    _mem_cache[cache_key] = disk
                    return disk
            except Exception:
                pass

    merged_watts:     dict[str, float] = {}
    merged_wh_day:    dict[str, float] = {}
    errors:           list[str]        = []
    tz:               str | None       = None

    async with httpx.AsyncClient(timeout=15) as client:
        for plane in fc.planes:
            label = plane.get("label", "plane")
            url = (
                f"https://api.forecast.solar/estimate"
                f"/{_num(fc.lat)}/{_num(fc.lon)}"
                f"/{int(_num(plane['tilt']))}/{int(_num(plane['azimuth']))}"
                f"/{_num(plane['kwp'])}"
            )
            try:
                r = await client.get(url)
                if r.status_code >= 400:
                    msg = f"HTTP {r.status_code}: {r.text[:160]}"
                    log.warning("Forecast.Solar %s → %s", url, msg)
                    errors.append(f"{label}: {msg}")
                r.raise_for_status()
                data = r.json()
                tz = tz or data.get("message", {}).get("info", {}).get("timezone")
                result = data.get("result", {})
                for ts, w in result.get("watts", {}).items():
                    merged_watts[ts] = merged_watts.get(ts, 0) + w
                for date, wh in result.get("watt_hours_day", {}).items():
                    merged_wh_day[date] = merged_wh_day.get(date, 0) + wh
            except Exception as e:
                log.warning("Forecast.Solar fetch failed for plane %s: %s", label, e)
                errors.append(f"{label}: {e}")

        # Fallback: if Forecast.Solar returned nothing, use Open-Meteo so the
        # user still gets an estimate (Forecast.Solar's PVGIS errors are common).
        source = "forecast.solar"
        if not merged_wh_day:
            log.info("Forecast.Solar empty — falling back to Open-Meteo")
            w2, wh2, e2, tz2 = await _fetch_openmeteo(fc, client)
            errors.extend(e2)
            if wh2:
                merged_watts, merged_wh_day = w2, wh2
                source = "open-meteo"
                tz = tz or tz2
                errors.append("Forecast.Solar unavailable — showing Open-Meteo estimate (less precise).")

    result = {
        "watts":          merged_watts,
        "watt_hours_day": merged_wh_day,
        "fetched_at":     int(time.time()),
        "planes":         fc.planes,
        "cache_key":      cache_key,
        "errors":         errors,
        "source":         source,
        "timezone":       tz,
    }
    _mem_cache[cache_key] = result
    try:
        _CACHE_FILE.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def current_hour_kwh(forecast: dict) -> float:
    """
    Forecasted production (kWh) for the hour happening right now, evaluated in
    the plant's local timezone (from the forecast response). 0 if none (night).
    """
    from datetime import datetime
    tz = forecast.get("timezone")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz)) if tz else datetime.now()
    except Exception:
        now = datetime.now()
    today, hh = now.strftime("%Y-%m-%d"), now.strftime("%H")
    best = 0.0
    for ts, w in forecast.get("watts", {}).items():
        if ts.startswith(today) and ts[11:13] == hh:
            best = max(best, float(w))
    return best / 1000.0


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
