"""Truck loads, dollars and availability: what each truck should hold for tomorrow's route.

Centres restock trucks between workdays, so tomorrow's load can be built from tomorrow's booked jobs.
Each held-back truck-day is replayed under three policies: today's fixed stock; today's stock topped
up with the day's carry lists; and the day's carry lists alone. The dollar figures are made up and
stated once, here, so a run on real data swaps in real ones.
"""
from collections import defaultdict

from staple.predict import Predictor
from staple.world import travel_minutes

REPEAT_VISIT = 165.0   # a second truck roll because a part was missing (made up)
ONE_DAY_PART = 4.0     # picking a part at the centre for one route, and taking it back if unused (made up)
HOLDING_RATE = 0.25    # yearly cost of money tied up in truck stock, as a share of its value (made up)
NEARBY_MINUTES = 30    # another truck is nearby when it is based within this drive of the job
LEAN_TOLERANCE = 2.0   # dollars a truck-day: the shortest list costing within this of the cheapest is recommended
KS = range(1, 9)
SHOWN_K = 3
TODAY, EVERYTHING = "today's trucks", "today's stock plus every part for the day's appliance types"
TOP_UP, LISTS_ONLY = "today's stock plus the day's lists", "the day's lists only"
CONTROL = "top-up from shuffled history"
MARKS = ("on this truck", "from a centre overnight", "on another truck nearby", "back-ordered",
         "needs a certified technician")


class Fleet:
    """The trucks as they stand today: what each holds, its space, who drives it, and the centres' stock."""

    def __init__(self, conn):
        self.cost = dict(conn.execute("SELECT part, cost FROM parts"))
        self.size = dict(conn.execute("SELECT part, size FROM parts"))
        self.locked = {p for (p,) in conn.execute("SELECT part FROM parts WHERE locked = 1")}
        self.certified = {t for (t,) in conn.execute("SELECT tech FROM techs WHERE certified = 1")}
        self.home = dict(conn.execute("SELECT tech, home_zone FROM techs"))
        self.capacity = dict(conn.execute("SELECT tech, capacity FROM trucks"))
        self.stock = defaultdict(set)
        for tech, part in conn.execute("SELECT t.tech, s.part FROM truck_stock s JOIN trucks t USING (truck)"):
            self.stock[tech].add(part)
        self.eta = dict(conn.execute("SELECT part, eta_days FROM centre_stock WHERE status = 'backorder'"))

    def may_carry(self, tech, part):
        """A locked part rides only with a certified technician; a back-ordered one only where it already is."""
        if part in self.locked and tech not in self.certified:
            return False
        return part not in self.eta or part in self.stock[tech]


def ranked_lists(predictor, jobs):
    """Every part for each job, likeliest first, in the predictor's own carry-list order."""
    out = {}
    for j in jobs:
        odds = predictor.odds(j["type"], j["family"], j["problem"])
        out[j["job"]] = sorted(odds.items(), key=lambda kv: (-kv[1], kv[0]))
    return out


def build_load(fleet, tech, day_jobs, lists, k, keep_stock):
    """Today's stock if kept, then the day's carry-list parts, likeliest first, while space allows."""
    # ponytail: a truck that holds a part is taken to hold enough of it, as the made-up history assumes;
    # count units per truck-day once real usage shows how often two calls on one route need the same part.
    load = set(fleet.stock[tech]) if keep_stock else set()
    space = sum(fleet.size[p] for p in load)
    wanted = {}
    for j in day_jobs:
        for part, p in lists[j["job"]][:k]:
            wanted[part] = max(wanted.get(part, 0.0), p)
    for part in sorted(wanted, key=lambda q: (-wanted[q], q)):
        if part not in load and fleet.may_carry(tech, part) and space + fleet.size[part] <= fleet.capacity[tech]:
            load.add(part)
            space += fleet.size[part]
    return load, space


def day_cost(fleet, load, fixed, misses):
    """One truck-day in dollars: repeat visits, parts carried for this day only, and money tied up in stock."""
    repeat = misses * REPEAT_VISIT
    handling = len(load - fixed) * ONE_DAY_PART
    holding = sum(fleet.cost[p] for p in load) * HOLDING_RATE / 365
    return repeat, handling, holding


def truck_days(jobs):
    days = defaultdict(list)
    for j in jobs:
        days[(j["day"], j["tech"])].append(j)
    return days


def replay(fleet, jobs, policies):
    """Every held-back truck-day under each policy, averaged per truck-day."""
    days = truck_days(jobs)
    rows = []
    for name, k, keep_stock, lists in policies:
        t = defaultdict(float)
        for (_, tech), day_jobs in days.items():
            if lists is None:
                load, space = fleet.stock[tech], sum(fleet.size[p] for p in fleet.stock[tech])
            else:
                load, space = build_load(fleet, tech, day_jobs, lists, k, keep_stock)
            covered = sum(1 for j in day_jobs if j["needed"] <= load)
            fixed = fleet.stock[tech] if keep_stock else set()
            repeat, handling, holding = day_cost(fleet, load, fixed, len(day_jobs) - covered)
            for key, v in (("jobs", len(day_jobs)), ("covered", covered), ("parts", len(load)), ("space", space),
                           ("value", sum(fleet.cost[p] for p in load)), ("one_day", len(load - fixed)),
                           ("repeat", repeat), ("handling", handling), ("holding", holding)):
                t[key] += v
        n = len(days)
        row = {"policy": name, "k": k, "truck_days": n, "coverage": t["covered"] / t["jobs"],
               **{key: t[key] / n for key in ("jobs", "parts", "space", "value", "one_day",
                                              "repeat", "handling", "holding")}}
        row["total"] = row["repeat"] + row["handling"] + row["holding"]
        # Inventory ROI here: repeat-visit dollars a year that the stock avoids, per dollar of stock on the truck.
        row["roi"] = t["covered"] / n * REPEAT_VISIT * 365 / row["value"]
        rows.append(row)
    return rows


def mark(fleet, tech, zone, part):
    """Where one carry-list part would come from for this job's technician."""
    if part in fleet.locked and tech not in fleet.certified:
        return "needs a certified technician"
    if part in fleet.stock[tech]:
        return "on this truck"
    if part not in fleet.eta:
        return "from a centre overnight"
    if any(o != tech and part in s and travel_minutes(fleet.home[o], zone) <= NEARBY_MINUTES
           for o, s in fleet.stock.items()):
        return "on another truck nearby"
    return "back-ordered"


def availability(fleet, jobs, lists, k=SHOWN_K):
    """The marks on every carry-list part, and how well a back-order flag on the list finds blocked jobs."""
    counts = defaultdict(int)
    flagged = blocked = caught = 0
    for j in jobs:
        carry = [p for p, _ in lists[j["job"]][:k]]
        marks = {p: mark(fleet, j["tech"], j["zone"], p) for p in set(carry) | j["needed"]}
        for p in carry:
            counts[marks[p]] += 1
        flag = any(marks[p] == "back-ordered" for p in carry)
        block = any(marks[p] == "back-ordered" for p in j["needed"])
        flagged += flag
        blocked += block
        caught += flag and block
    total = sum(counts.values())
    return {"marks": {m: counts[m] / total for m in MARKS}, "jobs": len(jobs),
            "flagged": flagged, "blocked": blocked, "caught": caught}


def run(conn, jobs):
    fleet = Fleet(conn)
    real = ranked_lists(Predictor(conn), jobs)
    shuffled = ranked_lists(Predictor(conn, shuffled=True, seed=7), jobs)
    every = max(len(v) for v in real.values())
    policies = [(TODAY, 0, True, None), (EVERYTHING, every, True, real)]
    policies += [(TOP_UP, k, True, real) for k in KS]
    policies += [(LISTS_ONLY, k, False, real) for k in KS]
    policies += [(CONTROL, k, True, shuffled) for k in KS]
    rows = replay(fleet, jobs, policies)
    avail = availability(fleet, jobs, real)

    conn.execute("DELETE FROM load_policy")
    conn.execute("DELETE FROM availability")
    conn.executemany("INSERT INTO load_policy VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(r["policy"], r["k"], r["truck_days"], r["jobs"], r["coverage"], r["parts"], r["space"],
                       r["value"], r["one_day"], r["repeat"], r["handling"], r["holding"], r["total"], r["roi"])
                      for r in rows])
    conn.executemany("INSERT INTO availability VALUES (?,?,?)",
                     [("mark", m, s) for m, s in avail["marks"].items()] +
                     [("jobs", m, avail[m]) for m in ("jobs", "flagged", "blocked", "caught")])
    conn.commit()
    best = {p: lean(rows, p) for p in (TOP_UP, LISTS_ONLY)}
    today, top = rows[0], best[TOP_UP]
    # The repeat-visit cost at which the top-up stops paying: its extra handling and holding over the misses it saves.
    break_even = ((top["handling"] + top["holding"] - today["holding"]) /
                  ((today["repeat"] - top["repeat"]) / REPEAT_VISIT))
    return {"rows": rows, "best": best, "today": today, "everything": rows[1], "break_even": break_even,
            "availability": avail, "fleet": fleet, "lists": real, "shuffled": shuffled}


def lean(rows, policy):
    """The shortest carry list whose daily cost is within LEAN_TOLERANCE of the policy's cheapest."""
    mine = sorted((r for r in rows if r["policy"] == policy), key=lambda r: r["k"])
    cheapest = min(r["total"] for r in mine)
    return next(r for r in mine if r["total"] <= cheapest + LEAN_TOLERANCE)
