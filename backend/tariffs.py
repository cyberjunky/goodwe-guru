"""
Electricity tariff configuration and financial calculations.

Supports:
  - Flat import/export rates
  - Time-of-use (peak / off-peak) import rates
  - Currency symbol + optional VAT
  - Payback tracking (enter system cost once)
"""

import json
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

from config import settings as cfg


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TouPeriod:
    """One time-of-use period (e.g. peak 07:00-23:00 at €0.35/kWh)."""
    name:       str   = "Peak"
    start_h:    int   = 7      # hour 0-23
    start_m:    int   = 0
    end_h:      int   = 23
    end_m:      int   = 0
    rate:       float = 0.30   # €/kWh


@dataclass
class TariffConfig:
    currency:          str         = "€"
    import_rate:       float       = 0.28    # flat import €/kWh (used when tou disabled)
    export_rate:       float       = 0.08    # feed-in / export €/kWh
    vat_pct:           float       = 0.0     # e.g. 21.0 for 21 % VAT (applied on top)
    tou_enabled:       bool        = False
    tou_periods:       list        = field(default_factory=lambda: [
        asdict(TouPeriod("Peak",       7,  0, 23,  0, 0.35)),
        asdict(TouPeriod("Off-peak",   23, 0,  7,  0, 0.18)),
    ])
    system_cost:       float       = 0.0     # €, for payback calc (0 = disabled)
    co2_grid_gkg:      float       = 0.295   # kg CO₂ per kWh from grid (EU avg)


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────
_TARIFF_FILE = Path(cfg.db_path).parent / "tariffs.json"


def load_tariffs() -> TariffConfig:
    if _TARIFF_FILE.exists():
        try:
            data = json.loads(_TARIFF_FILE.read_text())
            t = TariffConfig()
            for k, v in data.items():
                if hasattr(t, k):
                    setattr(t, k, v)
            return t
        except Exception:
            pass
    return TariffConfig()


def save_tariffs(t: TariffConfig):
    _TARIFF_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TARIFF_FILE.write_text(json.dumps(asdict(t), indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Rate lookup
# ─────────────────────────────────────────────────────────────────────────────
def current_import_rate(t: TariffConfig, dt: datetime | None = None) -> float:
    """Return the applicable import rate for the given datetime (now if None)."""
    if not t.tou_enabled or not t.tou_periods:
        return t.import_rate
    dt = dt or datetime.now()
    h, m = dt.hour, dt.minute
    mins = h * 60 + m
    for p in t.tou_periods:
        s = p["start_h"] * 60 + p["start_m"]
        e = p["end_h"]   * 60 + p["end_m"]
        if s < e:
            if s <= mins < e:
                return p["rate"]
        else:  # wraps midnight
            if mins >= s or mins < e:
                return p["rate"]
    return t.import_rate


# ─────────────────────────────────────────────────────────────────────────────
# Financial calculations
# ─────────────────────────────────────────────────────────────────────────────
def _vat(val: float, vat_pct: float) -> float:
    return val * (1 + vat_pct / 100)


def calc_financials(
    t: TariffConfig,
    e_imp: float,      # kWh imported from grid
    e_exp: float,      # kWh exported to grid
    e_solar: float,    # kWh total solar production
    e_load: float,     # kWh total home consumption
    e_bat_dis: float,  # kWh discharged from battery
    import_rate: float | None = None,
) -> dict:
    """
    Returns a dict with all financial and sustainability metrics.
    All monetary values are in the configured currency.
    """
    ir = import_rate if import_rate is not None else t.import_rate
    er = t.export_rate

    # What you paid for grid electricity
    import_cost = _vat(e_imp * ir, t.vat_pct)

    # Revenue from selling solar surplus
    export_revenue = e_exp * er                    # usually no VAT on revenue

    # kWh consumed from solar + battery (not imported)
    self_consumed = max(0.0, e_solar - e_exp)      # solar directly used
    bat_savings_kwh = e_bat_dis                    # battery discharged instead of importing

    # What those kWh would have cost on the grid
    self_consumed_savings = _vat(self_consumed * ir, t.vat_pct)
    bat_savings_value     = _vat(bat_savings_kwh * ir, t.vat_pct)

    total_savings = self_consumed_savings + bat_savings_value + export_revenue
    net_benefit   = total_savings - import_cost

    # Ratios
    self_sufficiency = (max(0.0, e_load - e_imp) / e_load * 100) if e_load > 0 else 0.0
    self_consumption = (self_consumed / e_solar * 100)            if e_solar > 0 else 0.0

    # CO₂ avoided (vs grid)
    co2_avoided_kg = self_consumed * t.co2_grid_gkg

    return {
        "currency":               t.currency,
        "import_rate":            ir,
        "export_rate":            er,
        "import_cost":            round(import_cost, 3),
        "export_revenue":         round(export_revenue, 3),
        "self_consumed_savings":  round(self_consumed_savings, 3),
        "bat_savings_value":      round(bat_savings_value, 3),
        "total_savings":          round(total_savings, 3),
        "net_benefit":            round(net_benefit, 3),
        "self_sufficiency_pct":   round(self_sufficiency, 1),
        "self_consumption_pct":   round(self_consumption, 1),
        "co2_avoided_kg":         round(co2_avoided_kg, 3),
    }
