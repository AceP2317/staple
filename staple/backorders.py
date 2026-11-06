"""The fleet back-order list: each part the centers cannot ship, ranked by the repeat visits it puts at risk
before it arrives.

Which booking will need a stuck part cannot be told in advance, but how many will can, so the action
is fleet-wide: expedite, source elsewhere, or move a truck's spare to where the demand is. The list
is back-tested: today's back-orders are treated as starting on the first held-back day, and the
forecast is set against the calls that then came.
"""
import datetime as dt
from collections import Counter, defaultdict

from staple.backtest import test_jobs
from staple.load import NEARBY_MINUTES, REPEAT_VISIT, Fleet, mark
from staple.world import ZONES, travel_minutes


def exposure(part_calls, type_calls, per_day, eta, stuck_share):
    """Calls expected to need a part before it ships, how many no truck nearby could cover, and the repeat-visit
    dollars that puts at risk: the part's share of its appliance's calls, times that appliance's calls a day, times
    the days until it ships."""
    expected = part_calls / type_calls * per_day * eta
    stuck = expected * stuck_share
    return expected, stuck, stuck * REPEAT_VISIT


def forecast(conn, fleet, start):
    """Per stuck part, from training only: calls expected before it arrives, and how many no truck could cover.
    Demand is seasonal, so a part's share of its appliance's calls is set against that appliance's calls per
    day in the same month of the training years."""
    month = f"{start.month:02d}"
    month_days = conn.execute("SELECT COUNT(DISTINCT day) FROM jobs WHERE split = 'train' AND substr(day, 6, 2) = ?",
                              (month,)).fetchone()[0]
    per_day = {a: n / month_days for a, n in conn.execute(
        "SELECT type, COUNT(*) FROM jobs WHERE split = 'train' AND substr(day, 6, 2) = ? GROUP BY type", (month,))}
    type_calls = dict(conn.execute("SELECT type, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY type"))
    calls = dict(conn.execute("""SELECT jp.part, COUNT(*) FROM job_parts jp JOIN jobs j USING (job)
                                 WHERE j.split = 'train' GROUP BY jp.part"""))
    part_type = dict(conn.execute("SELECT part, type FROM parts"))
    by_zone, by_zone_tech = defaultdict(Counter), defaultdict(Counter)
    for appliance, zone, tech, n in conn.execute(
            "SELECT type, zone, tech, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY type, zone, tech"):
        by_zone[appliance][zone] += n
        by_zone_tech[appliance][(zone, tech)] += n
    rows = []
    for part, eta in fleet.eta.items():
        appliance = part_type[part]
        holders = sorted(t for t, s in fleet.stock.items() if part in s)
        open_zones = [z for z in range(len(ZONES))
                      if not any(travel_minutes(fleet.home[h], z) <= NEARBY_MINUTES for h in holders)]
        # A call is stuck when its zone has no holder nearby and its own technician holds none. Technicians
        # mostly work near home, so the chance a holder takes the call is measured zone by zone.
        # ponytail: dispatch could still send a holder from farther away; the dispatch replay can price that.
        total = sum(by_zone[appliance].values())
        stuck_share = sum((by_zone[appliance][z] - sum(by_zone_tech[appliance][(z, h)] for h in holders)) / total
                          for z in open_zones)
        expected, stuck, at_risk = exposure(calls.get(part, 0), type_calls[appliance], per_day[appliance], eta, stuck_share)
        rows.append({"part": part, "eta": eta, "holders": holders, "open_zones": len(open_zones),
                     "expected": expected, "stuck": stuck, "at_risk": at_risk})
    return sorted(rows, key=lambda r: (-r["at_risk"], r["part"]))


def run(conn):
    """The fleet back-order list over the whole held-back period, written to backorder_list. It takes no job
    list, so no caller can leave the table holding a subset."""
    fleet, jobs = Fleet(conn), test_jobs(conn)
    start =dt.date.fromisoformat(min(j["day"] for j in jobs))
    rows = forecast(conn, fleet, start)
    for r in rows:
        until = str(start + dt.timedelta(days=r["eta"]))
        hit = [j for j in jobs if j["day"] < until and r["part"] in j["needed"]]
        r["actual"] = len(hit)
        r["actual_stuck"] = sum(1 for j in hit if mark(fleet, j["tech"], j["zone"], r["part"]) == "back-ordered")
    conn.execute("DELETE FROM backorder_list")
    conn.executemany("INSERT INTO backorder_list VALUES (?,?,?,?,?,?,?,?,?)",
                     [(r["part"], r["eta"], " ".join(r["holders"]), r["open_zones"], r["expected"], r["stuck"],
                       r["at_risk"], r["actual"], r["actual_stuck"]) for r in rows])
    conn.commit()
    return rows
