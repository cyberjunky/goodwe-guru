"""SQLite persistence for inverter snapshots and daily summaries."""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
        self.con.commit()

    def insert_snapshot(self, data: dict):
        import json
        ts = int(time.time())
        slim = {k: data[k] for k in SNAPSHOT_KEYS if k in data}
        self.con.execute("INSERT INTO snapshots (ts, data) VALUES (?,?)", (ts, json.dumps(slim)))
        self._upsert_daily(data)
        self.con.commit()
        self._prune()

    def _upsert_daily(self, data: dict):
        date = datetime.utcfromtimestamp(time.time()).strftime("%Y-%m-%d")
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
            date = datetime.utcnow().strftime("%Y-%m-%d")
            # return per-10-min averages from raw snapshots
            cutoff = int(time.time()) - 86400
            rows = cur.execute(
                "SELECT ts, data FROM snapshots WHERE ts > ? ORDER BY ts", (cutoff,)
            ).fetchall()
            import json
            result = []
            for r in rows:
                d = json.loads(r["data"])
                d["ts"] = datetime.utcfromtimestamp(r["ts"]).strftime("%H:%M")
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
                WHERE date >= date('now','-12 months')
                GROUP BY substr(date,1,7)
                ORDER BY ts
            """).fetchall()
            return [dict(r) for r in rows]

        return [{"ts": r["date"], **{k: r[k] for k in r.keys() if k != "date"}} for r in rows]

    def get_energy_flow(self, date: str | None = None) -> dict:
        """
        Integrate the day's raw snapshots into Sankey-style energy flows (kWh),
        decomposing each instant by a priority model:
          solar → load, then battery charge, then grid export
          battery discharge → load
          grid import → load, then battery charge
        Works without inverter import/export counters (which ES lacks).
        """
        import json
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        try:
            start_dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            date = start_dt.strftime("%Y-%m-%d")
        start = int(start_dt.timestamp())
        end   = int((start_dt + timedelta(days=1)).timestamp())

        rows = self.con.execute(
            "SELECT ts, data FROM snapshots WHERE ts>=? AND ts<? ORDER BY ts",
            (start, end),
        ).fetchall()

        links = {"solar_load": 0.0, "solar_batt": 0.0, "solar_grid": 0.0,
                 "batt_load": 0.0, "grid_load": 0.0, "grid_batt": 0.0}
        prev_ts = None
        for r in rows:
            ts = r["ts"]
            d  = json.loads(r["data"])
            if prev_ts is not None:
                dt = ts - prev_ts
                if 0 < dt <= 120:                       # cap gaps so outages don't inflate
                    h     = dt / 3600.0
                    solar = max(float(d.get("ppv", 0) or 0), 0)
                    pb    = float(d.get("pbattery1", 0) or 0)   # >0 charge, <0 discharge
                    bc, bd = max(pb, 0), max(-pb, 0)
                    pg    = float(d.get("pgrid", 0) or 0)       # >0 import, <0 export (normalised)
                    gi    = max(pg, 0)
                    load  = max(float(d.get("load_ptotal", 0) or 0), 0)

                    L = load
                    s_load = min(solar, L);          s = solar - s_load; L -= s_load
                    b_load = min(bd, L);                                  L -= b_load
                    g_load = min(gi, L);             gi2 = gi - g_load;   L -= g_load
                    s_batt = min(s, bc);             s -= s_batt; bc2 = bc - s_batt
                    s_grid = s                                            # leftover solar exports
                    g_batt = min(gi2, bc2)

                    links["solar_load"] += s_load * h
                    links["solar_batt"] += s_batt * h
                    links["solar_grid"] += s_grid * h
                    links["batt_load"]  += b_load * h
                    links["grid_load"]  += g_load * h
                    links["grid_batt"]  += g_batt * h
            prev_ts = ts

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
                "destinations": destinations, "samples": len(rows)}

    def get_peak_pv(self) -> float:
        """Highest PV power (W) ever recorded — a proxy for installed array size."""
        row = self.con.execute("SELECT MAX(ppv_max) AS m FROM daily_summary").fetchone()
        return float(row["m"] or 0) if row else 0.0

    def get_today_summary(self) -> dict:
        """Return today's daily_summary row as a dict (for Telegram daily message)."""
        date = datetime.utcnow().strftime("%Y-%m-%d")
        row = self.con.execute(
            "SELECT * FROM daily_summary WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else {}

    def get_cumulative_savings(self, tariffs) -> float:
        """
        Sum all historical net benefits to compute total savings for payback tracking.
        Requires tariffs object to do per-row calc.
        """
        from tariffs import calc_financials
        rows = self.con.execute(
            "SELECT e_day, e_day_exp, e_day_imp, e_load_day, e_bat_discharge_day "
            "FROM daily_summary ORDER BY date"
        ).fetchall()
        total = 0.0
        for r in rows:
            fin = calc_financials(
                tariffs,
                e_imp    = r[2] or 0,
                e_exp    = r[1] or 0,
                e_solar  = r[0] or 0,
                e_load   = r[3] or 0,
                e_bat_dis= r[4] or 0,
            )
            total += fin["net_benefit"]
        return total

    def close(self):
        self.con.close()
