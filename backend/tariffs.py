"""
Electricity tariff configuration and financial calculations.

Supports:
  - Flat import/export rates
  - Time-of-use (peak / off-peak) import rates
  - Currency symbol + optional VAT
  - Payback tracking (enter system cost once)
"""

import json
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


def _hour_rate(t: TariffConfig, hour: int) -> float:
    """Import rate in effect for a local clock hour (sampled at its midpoint)."""
    return current_import_rate(t, datetime(2000, 1, 1, hour, 30))


def mean_import_rate(t: TariffConfig) -> float:
    """24-hour average import rate — values self-consumed kWh under TOU."""
    if not t.tou_enabled or not t.tou_periods:
        return t.import_rate
    return sum(_hour_rate(t, h) for h in range(24)) / 24


def calc_financials(
    t: TariffConfig,
    e_imp: float,      # kWh imported from grid
    e_exp: float,      # kWh exported to grid
    e_solar: float,    # kWh total solar production
    e_load: float,     # kWh total home consumption
    e_bat_dis: float,  # kWh discharged from battery
    e_bat_chg: float = 0.0,             # kWh charged into the battery
    imp_by_hour: list | None = None,    # kWh imported per local hour (TOU pricing)
) -> dict:
    """
    Returns a dict with all financial and sustainability metrics.
    All monetary values are in the configured currency.

    Solar that goes through the battery is valued ONCE, when it is
    discharged (e_bat_dis) — the direct-use part excludes what was charged.
    Counting `solar - export` plus the discharge credited it twice.
    """
    ir = mean_import_rate(t)
    er = t.export_rate

    # What you paid for grid electricity — hour by hour under TOU when the
    # hourly split is known (days with snapshots), else at the mean rate.
    if t.tou_enabled and imp_by_hour and len(imp_by_hour) == 24:
        import_net = sum(float(kwh or 0) * _hour_rate(t, h) for h, kwh in enumerate(imp_by_hour))
    else:
        import_net = e_imp * ir
    import_cost = _vat(import_net, t.vat_pct)

    # Revenue from selling solar surplus
    export_revenue = e_exp * er                    # usually no VAT on revenue

    solar_used      = max(0.0, e_solar - e_exp)            # into the house + battery
    direct_used     = max(0.0, solar_used - e_bat_chg)     # straight into the house
    bat_savings_kwh = e_bat_dis                    # battery discharged instead of importing

    # What those kWh would have cost on the grid
    self_consumed_savings = _vat(direct_used * ir, t.vat_pct)
    bat_savings_value     = _vat(bat_savings_kwh * ir, t.vat_pct)

    total_savings = self_consumed_savings + bat_savings_value + export_revenue
    net_benefit   = total_savings - import_cost

    # Ratios
    self_sufficiency = (max(0.0, e_load - e_imp) / e_load * 100) if e_load > 0 else 0.0
    self_consumption = (solar_used / e_solar * 100)               if e_solar > 0 else 0.0

    # CO₂ avoided (vs grid)
    co2_avoided_kg = solar_used * t.co2_grid_gkg

    return {
        "currency":               t.currency,
        "import_rate":            round(ir, 4),
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


def day_financials(t: TariffConfig, row: dict) -> dict:
    """calc_financials for one daily_summary row (or a 12-month aggregate)."""
    hours = row.get("imp_by_hour")
    if isinstance(hours, str):
        try:
            hours = json.loads(hours)
        except ValueError:
            hours = None

    def f(k: str) -> float:
        return float(row.get(k) or 0)

    return calc_financials(
        t, e_imp=f("e_day_imp"), e_exp=f("e_day_exp"), e_solar=f("e_day"),
        e_load=f("e_load_day"), e_bat_dis=f("e_bat_discharge_day"),
        e_bat_chg=f("e_bat_charge_day"), imp_by_hour=hours)
