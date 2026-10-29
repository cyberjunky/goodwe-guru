"""SQLite persistence for inverter snapshots and daily summaries.

Dates are LOCAL calendar days throughout (the inverter's own day counters
reset at local midnight). Rows written before 2026-10-29 used UTC dates; for
cumulative MAX-merged counters that only shifts the 0-2 h tail of each day.
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import settings as cfg

SNAPSHOT_KEYS = [
    "ppv", "ppv1", "ppv2", "ppv3", "ppv4",
    "pgrid", "pgrid2", "pgrid3",
    "pbattery1", "battery_soc",
    "load_ptotal", "backup_ptotal",
    "e_day", "e_day_exp", "e_day_imp",
    "e_load_day", "e_bat_charge_day", "e_bat_discharge_day",
    "vgrid", "fgrid", "temperature", "battery_temperature",
]


class Database:
    def __init__(self):
        path = Path(cfg.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def migrate(self):
        cur = self.con.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       INTEGER NOT NULL,
                data     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

            CREATE TABLE IF NOT EXISTS forecast_log (
                date          TEXT PRIMARY KEY,
                forecast_kwh  REAL,
                actual_kwh    REAL
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                date              TEXT PRIMARY KEY,
                e_day             REAL DEFAULT 0,
                e_day_exp         REAL DEFAULT 0,
                e_day_imp         REAL DEFAULT 0,
                e_load_day        REAL DEFAULT 0,
                e_bat_charge_day  REAL DEFAULT 0,
                e_bat_discharge_day REAL DEFAULT 0,
                ppv_max           REAL DEFAULT 0,
                pgrid_min         REAL DEFAULT 0,
                pgrid_max         REAL DEFAULT 0
            );
        """)
        cols = {r[1] for r in cur.execute("PRAGMA table_info(daily_summary)")}
        if "imp_by_hour" not in cols:
            # 24 local-hour import buckets (kWh, JSON) derived from snapshots —
            # what time-of-use pricing needs. NULL for days without snapshots.
            cur.execute("ALTER TABLE daily_summary ADD COLUMN imp_by_hour TEXT")
        self.con.commit()

    def insert_snapshot(self, data: dict):
        ts = int(time.time())
        slim = {k: data[k] for k in SNAPSHOT_KEYS if k in data}
        self.con.execute("INSERT INTO snapshots (ts, data) VALUES (?,?)", (ts, json.dumps(slim)))
        self._upsert_daily(data)
        self.con.commit()
        self._prune()

    def _upsert_daily(self, data: dict):
        date = datetime.now().strftime("%Y-%m-%d")
        self.con.execute("""
            INSERT INTO daily_summary(date, e_day, e_day_exp, e_day_imp, e_load_day,
                e_bat_charge_day, e_bat_discharge_day, ppv_max, pgrid_min, pgrid_max)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                e_day             = MAX(e_day, excluded.e_day),
                e_day_exp         = MAX(e_day_exp, excluded.e_day_exp),
                e_day_imp         = MAX(e_day_imp, excluded.e_day_imp),
                e_load_day        = MAX(e_load_day, excluded.e_load_day),
                e_bat_charge_day  = MAX(e_bat_charge_day, excluded.e_bat_charge_day),
                e_bat_discharge_day = MAX(e_bat_discharge_day, excluded.e_bat_discharge_day),
                ppv_max           = MAX(ppv_max, excluded.ppv_max),
                pgrid_min         = MIN(pgrid_min, excluded.pgrid_min),
                pgrid_max         = MAX(pgrid_max, excluded.pgrid_max)
        """, (
            date,
            data.get("e_day", 0), data.get("e_day_exp", 0), data.get("e_day_imp", 0),
            data.get("e_load_day", 0), data.get("e_bat_charge_day", 0),
            data.get("e_bat_discharge_day", 0),
            data.get("ppv", 0), data.get("pgrid", 0), data.get("pgrid", 0),
        ))

    def _prune(self):
        cutoff = int(time.time()) - 7 * 24 * 3600   # keep 7 days of raw snapshots
        self.con.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))

    def get_history(self, range_: str) -> list[dict]:
        cur = self.con.cursor()
        if range_ == "today":
            # raw snapshots of the last 24 h (Telegram power chart)
            cutoff = int(time.time()) - 86400
            rows = cur.execute(
                "SELECT ts, data FROM snapshots WHERE ts > ? ORDER BY ts", (cutoff,)
            ).fetchall()
            result = []
            for r in rows:
                d = json.loads(r["data"])
                d["ts"] = datetime.fromtimestamp(r["ts"]).strftime("%H:%M")
                result.append(d)
            return result
        elif range_ == "7d":
            rows = cur.execute(
                "SELECT * FROM daily_summary ORDER BY date DESC LIMIT 7"
            ).fetchall()
        elif range_ == "30d":
            rows = cur.execute(
                "SELECT * FROM daily_summary ORDER BY date DESC LIMIT 30"
            ).fetchall()
        else:  # 12m — group by month
            rows = cur.execute("""
                SELECT substr(date,1,7) as ts,
                    SUM(e_day) as e_day, SUM(e_day_exp) as e_day_exp,
                    SUM(e_day_imp) as e_day_imp, SUM(e_load_day) as e_load_day,
                    SUM(e_bat_charge_day) as e_bat_charge_day,
                    SUM(e_bat_discharge_day) as e_bat_discharge_day
                FROM daily_summary
                WHERE date >= date('now','localtime','-12 months')
                GROUP BY substr(date,1,7)
                ORDER BY ts
            """).fetchall()
            return [dict(r) for r in rows]

        return [{"ts": r["date"], **{k: r[k] for k in r.keys() if k != "date"}} for r in rows]

    @staticmethod
    def _day_bounds(date: str | None) -> tuple[str, int, int]:
        """(date, start_ts, end_ts) of a local calendar day; today if unparsable."""
        try:
            start_dt = datetime.strptime(date or "", "%Y-%m-%d")
        except ValueError:
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=1)
        return start_dt.strftime("%Y-%m-%d"), int(start_dt.timestamp()), int(end_dt.timestamp())

    def _integrate(self, start: int, end: int) -> tuple[dict, list[float], int]:
        """
        Integrate raw snapshots in [start, end) into energy flows (Wh), using a
        priority model per instant:
          solar → load, then battery charge, then grid export
          battery discharge → load
          grid import → load, then battery charge
        Also returns grid import per local hour (Wh) for time-of-use pricing.
        Works without inverter import/export counters (which ES lacks).
        """
        rows = self.con.execute(
            "SELECT ts, data FROM snapshots WHERE ts>=? AND ts<? ORDER BY ts",
            (start, end),
        ).fetchall()

        links = {"solar_load": 0.0, "solar_batt": 0.0, "solar_grid": 0.0,
                 "batt_load": 0.0, "grid_load": 0.0, "grid_batt": 0.0}
        imp_by_hour = [0.0] * 24
        prev_ts = None
        for r in rows:
            ts = r["ts"]
            d  = json.loads(r["data"])
            if prev_ts is not None:
                dt = ts - prev_ts
                if 0 < dt <= 120:                       # cap gaps so outages don't inflate
                    h     = dt / 3600.0
                    solar = max(float(d.get("ppv", 0) or 0), 0)
                    pg    = float(d.get("pgrid", 0) or 0)       # >0 import, <0 export (normalised)
                    gi, ge = max(pg, 0), max(-pg, 0)
                    load  = max(float(d.get("load_ptotal", 0) or 0), 0)

                    # Derive battery power from energy balance, NOT pbattery1 (whose
                    # sign convention is platform-dependent and was wrong on ES):
                    #   solar + grid_import + batt_discharge = load + grid_export + batt_charge
                    # → batt_net = solar + pg - load  (>0 charge, <0 discharge)
                    batt_net = solar + pg - load
                    bc, bd = max(batt_net, 0), max(-batt_net, 0)

                    L = load
                    s_load = min(solar, L);          s = solar - s_load; L -= s_load
                    b_load = min(bd, L);                                  L -= b_load
                    g_load = min(gi, L);             gi2 = gi - g_load;   L -= g_load
                    s_batt = min(s, bc);             s -= s_batt; bc2 = bc - s_batt
                    s_grid = min(s, ge)                                   # leftover solar → export
                    g_batt = min(gi2, bc2)

                    links["solar_load"] += s_load * h
                    links["solar_batt"] += s_batt * h
                    links["solar_grid"] += s_grid * h
                    links["batt_load"]  += b_load * h
                    links["grid_load"]  += g_load * h
                    links["grid_batt"]  += g_batt * h
                    imp_by_hour[datetime.fromtimestamp(ts).hour] += gi * h
            prev_ts = ts
        return links, imp_by_hour, len(rows)

    def get_energy_flow(self, date: str | None = None) -> dict:
        """Sankey-style energy flows (kWh) for one local day — see _integrate."""
        date, start, end = self._day_bounds(date)
        links, _, samples = self._integrate(start, end)
        links = {k: round(v / 1000, 3) for k, v in links.items()}   # Wh → kWh
        sources = {
            "solar":   round(links["solar_load"] + links["solar_batt"] + links["solar_grid"], 3),
            "battery": round(links["batt_load"], 3),
            "grid":    round(links["grid_load"] + links["grid_batt"], 3),
        }
        destinations = {
            "load":    round(links["solar_load"] + links["batt_load"] + links["grid_load"], 3),
            "battery": round(links["solar_batt"] + links["grid_batt"], 3),
            "grid":    round(links["solar_grid"], 3),
        }
        return {"date": date, "links": links, "sources": sources,
                "destinations": destinations, "samples": samples}

    def update_derived(self, date: str | None = None) -> None:
        """
        Fill a day's import/export/load/battery energies from snapshots.

        ES has no daily import/export counters (they read 0), so without this
        History, Finance and the dashboard's "today" figures were all 0 — and
        Finance counted every kWh of solar as self-consumed. MAX-merged, like
        the inverter counters: where a platform does report them the larger
        of the two wins, and a day whose early snapshots were already pruned
        keeps its earlier, more complete value.
        """
        date, start, end = self._day_bounds(date)
        links, imp_by_hour, samples = self._integrate(start, end)
        if samples < 2:
            return

        def kwh(*ks):
            return round(sum(links[k] for k in ks) / 1000, 3)

        self.con.execute(
            "INSERT INTO daily_summary(date) VALUES(?) ON CONFLICT(date) DO NOTHING", (date,))
        self.con.execute("""
            UPDATE daily_summary SET
                e_day_imp           = MAX(e_day_imp, ?),
                e_day_exp           = MAX(e_day_exp, ?),
                e_load_day          = MAX(e_load_day, ?),
                e_bat_charge_day    = MAX(e_bat_charge_day, ?),
                e_bat_discharge_day = MAX(e_bat_discharge_day, ?),
                imp_by_hour         = ?
            WHERE date = ?
        """, (
            kwh("grid_load", "grid_batt"), kwh("solar_grid"),
            kwh("solar_load", "batt_load", "grid_load"),
            kwh("solar_batt", "grid_batt"), kwh("batt_load"),
            json.dumps([round(v / 1000, 4) for v in imp_by_hour]), date,
        ))
        self.con.commit()

    def snapshot_dates(self) -> list[str]:
        """Local dates that still have raw snapshots (for the startup backfill)."""
        row = self.con.execute("SELECT MIN(ts), MAX(ts) FROM snapshots").fetchone()
        if not row or row[0] is None:
            return []
        d, last = datetime.fromtimestamp(row[0]).date(), datetime.fromtimestamp(row[1]).date()
        out = []
        while d <= last:
            out.append(d.isoformat())
            d += timedelta(days=1)
        return out

    def energy_totals(self) -> dict:
        """Today's derived day energies + lifetime import/export sums (kWh)."""
        today = self.get_today_summary()
        tot = self.con.execute(
            "SELECT SUM(e_day_imp), SUM(e_day_exp) FROM daily_summary").fetchone()
        out = {k: float(today.get(k) or 0) for k in
               ("e_day_imp", "e_day_exp", "e_load_day", "e_bat_charge_day", "e_bat_discharge_day")}
        out["e_total_imp"] = round(float(tot[0] or 0), 2)
        out["e_total_exp"] = round(float(tot[1] or 0), 2)
        return out

    def record_forecast(self, date: str, forecast_kwh: float):
        """Store the day's forecast once (first capture of the day wins)."""
        self.con.execute(
            "INSERT INTO forecast_log(date, forecast_kwh) VALUES(?,?) "
            "ON CONFLICT(date) DO NOTHING", (date, round(float(forecast_kwh), 2)))
        self.con.commit()

    def record_actual(self, date: str, actual_kwh: float):
        self.con.execute(
            "INSERT INTO forecast_log(date, actual_kwh) VALUES(?,?) "
            "ON CONFLICT(date) DO UPDATE SET actual_kwh=MAX(actual_kwh, excluded.actual_kwh)",
            (date, round(float(actual_kwh), 2)))
        self.con.commit()

    def repair_forecast_actuals(self):
        """Backfill forecast_log.actual_kwh from daily_summary.e_day (MAX wins)."""
        self.con.execute("""
            UPDATE forecast_log
            SET actual_kwh = (
                SELECT e_day FROM daily_summary WHERE daily_summary.date = forecast_log.date
            )
            WHERE EXISTS (
                SELECT 1 FROM daily_summary
                WHERE daily_summary.date = forecast_log.date
                  AND daily_summary.e_day > COALESCE(forecast_log.actual_kwh, 0)
            )
        """)
        self.con.commit()

    def get_forecast_accuracy(self, n: int = 14) -> list[dict]:
        rows = self.con.execute(
            "SELECT date, forecast_kwh, actual_kwh FROM forecast_log "
            "WHERE forecast_kwh IS NOT NULL ORDER BY date DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def get_day_series(self, date: str | None = None, bucket_min: int = 5) -> dict:
        """
        Bucketed power-over-time for one day + a derived battery state per bucket
        (charge / discharge / hold / idle). Battery is from energy balance
        (solar + pgrid - load), so it's independent of the pbattery1 sign.
        """
        date, start, end = self._day_bounds(date)
        bucket = max(1, bucket_min) * 60

        rows = self.con.execute(
            "SELECT ts, data FROM snapshots WHERE ts>=? AND ts<? ORDER BY ts",
            (start, end)).fetchall()

        agg: dict[int, dict] = {}
        for r in rows:
            d = json.loads(r["data"])
            b = (r["ts"] - start) // bucket
            a = agg.setdefault(b, {"ppv": 0.0, "pgrid": 0.0, "load": 0.0, "soc": 0.0, "n": 0})
            a["ppv"]  += float(d.get("ppv", 0) or 0)
            a["pgrid"] += float(d.get("pgrid", 0) or 0)
            a["load"] += float(d.get("load_ptotal", 0) or 0)
            a["soc"]  += float(d.get("battery_soc", 0) or 0)
            a["n"]    += 1

        series = []
        for b in sorted(agg):
            a = agg[b]; n = a["n"] or 1
            solar, pg, load, soc = a["ppv"]/n, a["pgrid"]/n, a["load"]/n, a["soc"]/n
            bnet = solar + pg - load   # >0 charge, <0 discharge
            if   bnet >  100: state = "charge"
            elif bnet < -100: state = "discharge"
            elif pg   >  100: state = "hold"     # battery idle while grid covers the house
            else:             state = "idle"
            series.append({
                "t":       datetime.fromtimestamp(start + b * bucket).strftime("%H:%M"),
                "solar":   round(solar / 1000, 3),
                "load":    round(load / 1000, 3),
                "grid":    round(pg / 1000, 3),
                "battery": round(bnet / 1000, 3),
                "soc":     round(soc),
                "state":   state,
            })
        return {"date": date, "series": series}

    def get_peak_pv(self) -> float:
        """Highest PV power (W) ever recorded — a proxy for installed array size."""
        row = self.con.execute("SELECT MAX(ppv_max) AS m FROM daily_summary").fetchone()
        return float(row["m"] or 0) if row else 0.0

    def daily_rows(self, days: int) -> list[dict]:
        """daily_summary rows of the last `days` local days, oldest first."""
        since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = self.con.execute(
            "SELECT * FROM daily_summary WHERE date >= ? ORDER BY date", (since,)).fetchall()
        return [dict(r) for r in rows]

    def get_today_summary(self) -> dict:
        """Return today's daily_summary row as a dict (for Telegram daily message)."""
        date = datetime.now().strftime("%Y-%m-%d")
        row = self.con.execute(
            "SELECT * FROM daily_summary WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else {}

    def get_cumulative_savings(self, tariffs) -> float:
        """Sum all historical net benefits, for payback tracking."""
        from tariffs import day_financials
        rows = self.con.execute("SELECT * FROM daily_summary ORDER BY date").fetchall()
        return sum(day_financials(tariffs, dict(r))["net_benefit"] for r in rows)

    def close(self):
        self.con.close()
