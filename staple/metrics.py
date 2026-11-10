"""The measures as plain SQL files in sql/metrics/, run against the one SQLite file.

The pages are built from these results, so every number on them comes from a query a reader can open and
run; each result is also written to out/metrics/<name>.csv. The made-up dollars and rates the queries need are
written to the assumptions table from the code's own constants, never typed into the SQL a second time.
"""
import csv
from pathlib import Path

from staple import db, dispatch, load, triage, world

SQL = Path(__file__).resolve().parent.parent / "sql" / "metrics"
FALLS_BACK = 0.25   # a control falls back when it keeps less than this share of the gain it tests (design choice)


def assumptions():
    rows = [("repeat_visit", load.REPEAT_VISIT, "dollars for a repeat visit because the first did not fix it"),
            ("move", load.MOVE, "dollars to put one part on a truck overnight, or take one off"),
            ("falls_back", FALLS_BACK, "share of a gain below which a control counts as fallen back"),
            ("carry_list_quoted", load.SHOWN_K, "parts on the carry list the report quotes"),
            ("ai_confidence", triage.BLEND_BELOW, "confidence below which a reading leans on the appliance's odds and a question is asked"),
            ("holding_rate", load.HOLDING_RATE, "yearly cost of money tied up in truck stock, per dollar held"),
            ("minute", dispatch.MINUTE, "dollars for one minute of a technician and truck"),
            ("skill_pull", dispatch.SKILL_PULL, "jobs at the appliance's average rate mixed into a technician's own"),
            ("unplanned", load.UNPLANNED, "share of calls added or moved the same day"),
            ("sick", dispatch.SICK, "chance a technician calls out sick on a given day"),
            ("breakdown", dispatch.BREAKDOWN, "chance a truck breaks down on a given day"),
            ("same_day_until", dispatch.SAME_DAY_UNTIL, "minutes after 08:00 within which an unplanned call comes in"),
            ("backorder_starts", world.BACKORDER_STARTS, "chance a day that a part goes on back-order"),
            ("order_next_day", world.ORDER_NEXT_DAY, "share of missing parts ordered the morning after the visit"),
            ("day_minutes", dispatch.DAY, "minutes in a working day, 08:00 to 17:00"),
            ("truck_capacity", world.CAPACITY, "space units on one truck"),
            ("stock_fill", world.STOCK_FILL, "how full today's trucks are kept"),
            ("backorder_flag", load.BACKORDER_FLAG, "odds of needing a stuck part at which a booking is flagged")]
    return rows + [(f"repair_minutes.{t}", m, f"minutes an average technician takes on a {t}")
                   for t, m in dispatch.REPAIR.items()]


def read(conn, name):
    """One metric query's rows as dictionaries, for a step that acts on the same rows the pages show."""
    cur = conn.execute((SQL / f"{name}.sql").read_text(encoding="utf-8"))
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur]


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
