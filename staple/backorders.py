"""The fleet back-order list: each part the centers cannot ship, ranked by the repeat visits it puts at risk
before it ships.

Which booking will need a stuck part cannot be told in advance, but how many will can, so the action
is fleet-wide: expedite, source elsewhere, or move a truck's spare to where the demand is. A back-order
starts on one day and ships on another, so the list is made again for every held-back day: a part drops
off the day it ships, and the days left count down until then. The list is back-tested: each forecast is
set against the calls that then came before the part shipped.
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


class Demand:
    """What the training history says about demand, counted once and read for every day's list."""

    def __init__(self, conn):
        self.month_days = dict(conn.execute(
            "SELECT substr(day, 6, 2), COUNT(DISTINCT day) FROM jobs WHERE split = 'train' GROUP BY 1"))
        self.month_calls = {(m, a): n for m, a, n in conn.execute(
            "SELECT substr(day, 6, 2), type, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY 1, 2")}
        self.type_calls = dict(conn.execute("SELECT type, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY type"))
        self.part_calls = dict(conn.execute("""SELECT jp.part, COUNT(*) FROM job_parts jp JOIN jobs j USING (job)
                                               WHERE j.split = 'train' GROUP BY jp.part"""))
        self.part_type = dict(conn.execute("SELECT part, type FROM parts"))
        self.by_zone, self.by_zone_tech = defaultdict(Counter), defaultdict(Counter)
        for appliance, zone, tech, n in conn.execute(
                "SELECT type, zone, tech, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY type, zone, tech"):
            self.by_zone[appliance][zone] += n
            self.by_zone_tech[appliance][(zone, tech)] += n

    def per_day(self, appliance, day):
        """An appliance's calls a day in the same month of the training years, because demand is seasonal."""
        month = day[5:7]
        return self.month_calls.get((month, appliance), 0) / self.month_days[month]


def forecast(demand, fleet, day):
    """One day's list, from training only: per stuck part, the calls expected before it ships and how many no
    truck could cover, costliest first."""
    rows = []
    for part, days_left in fleet.stuck(day).items():
        appliance = demand.part_type[part]
        holders = sorted(t for t, s in fleet.stock.items() if part in s)
        open_zones = [z for z in range(len(ZONES))
                      if not any(travel_minutes(fleet.home[h], z) <= NEARBY_MINUTES for h in holders)]
        # A call is stuck when its zone has no holder nearby and its own technician holds none. Technicians
        # mostly work near home, so the chance a holder takes the call is measured zone by zone.
        # ponytail: dispatch could still send a holder from farther away; the dispatch replay can price that.
        by_zone, by_zone_tech = demand.by_zone[appliance], demand.by_zone_tech[appliance]
        total = sum(by_zone.values())
        stuck_share = sum((by_zone[z] - sum(by_zone_tech[(z, h)] for h in holders)) / total for z in open_zones)
        expected, stuck, at_risk = exposure(demand.part_calls.get(part, 0), demand.type_calls[appliance],
                                            demand.per_day(appliance, day), days_left, stuck_share)
        rows.append({"day": day, "part": part, "days_left": days_left, "holders": holders,
                     "open_zones": len(open_zones), "expected": expected, "stuck": stuck, "at_risk": at_risk})
    return sorted(rows, key=lambda r: (-r["at_risk"], r["part"]))


def run(conn):
    """The back-order list for every held-back day, written to backorder_list. It takes no job list, so no caller
    can leave the table holding a subset."""
    fleet, jobs, demand = Fleet(conn), test_jobs(conn), Demand(conn)
    days = sorted({j["day"] for j in jobs})
    after = str(dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1))
    needing = defaultdict(list)
    for j in jobs:
        for part in j["needed"]:
            needing[part].append(j)
    rows, listed = [], set()
    for day in days:
        today = dt.date.fromisoformat(day)
        for r in forecast(demand, fleet, day):
            ships = str(today + dt.timedelta(days=r["days_left"]))
            hit = [j for j in needing[r["part"]] if day <= j["day"] < ships]
            r["actual"] = len(hit)
            r["actual_stuck"] = sum(1 for j in hit if mark(fleet, j["tech"], j["zone"], r["part"],
                                                           fleet.stuck(j["day"])) == "back-ordered")
            # The forecast is scored once per back-order, on the first day it is listed, and only when the part
            # ships inside the held-back days, so the calls that came are all counted.
            r["first"] = (r["part"], ships) not in listed
            r["whole"] = ships <= after
            listed.add((r["part"], ships))
            rows.append(r)
    conn.execute("DELETE FROM backorder_list")
    conn.executemany("INSERT INTO backorder_list VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(r["day"], r["part"], r["days_left"], " ".join(r["holders"]), r["open_zones"], r["expected"],
                       r["stuck"], r["at_risk"], r["actual"], r["actual_stuck"], int(r["first"]), int(r["whole"]))
                      for r in rows])
    conn.commit()
    return rows
