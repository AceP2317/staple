"""The job description's metrics as plain SQL files in sql/metrics/, run against the one SQLite file.

Each result is also written to out/metrics/<name>.csv, which the report reads, so every number on the
report comes from a query a reader can open and run. The made-up dollars and rates the queries need are
written to the assumptions table from the code's own constants, never typed into the SQL a second time.
"""
import csv
from pathlib import Path

from staple import db, dispatch, load, world

SQL = Path(__file__).resolve().parent.parent / "sql" / "metrics"


def assumptions():
    rows = [("repeat_visit", load.REPEAT_VISIT, "dollars for a second visit because the first did not fix it"),
            ("move", load.MOVE, "dollars to put one part on a truck overnight, or take one off"),
            ("holding_rate", load.HOLDING_RATE, "yearly cost of money tied up in truck stock, per dollar held"),
            ("minute", dispatch.MINUTE, "dollars for one minute of a technician and truck"),
            ("unplanned", load.UNPLANNED, "share of calls added or moved the same day"),
            ("sick", dispatch.SICK, "chance a technician calls out sick on a given day"),
            ("breakdown", dispatch.BREAKDOWN, "chance a truck breaks down on a given day"),
            ("day_minutes", dispatch.DAY, "minutes in a working day, 08:00 to 17:00"),
            ("truck_capacity", world.CAPACITY, "space units on one truck"),
            ("stock_fill", world.STOCK_FILL, "how full today's trucks are kept"),
            ("backorder_flag", load.BACKORDER_FLAG, "odds of needing a stuck part at which a booking is flagged")]
    return rows + [(f"repair_minutes.{t}", m, f"minutes an average technician takes on a {t}")
                   for t, m in dispatch.REPAIR.items()]


def run(conn, out=db.OUT / "metrics"):
    conn.execute("DELETE FROM assumptions")
    conn.executemany("INSERT INTO assumptions VALUES (?,?,?)", assumptions())
    conn.commit()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for path in sorted(SQL.glob("*.sql")):
        cur = conn.execute(path.read_text(encoding="utf-8"))
        columns, rows = [d[0] for d in cur.description], cur.fetchall()
        with open(out / f"{path.stem}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        results[path.stem] = (columns, rows)
    return results
