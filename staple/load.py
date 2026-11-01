"""Truck loads, dollars and availability: what each truck should hold for tomorrow's route.

Centres restock trucks between workdays, so tomorrow's load can be built from tomorrow's booked calls.
Each held-back truck-day is replayed, truck by truck in date order, under several policies: today's
fixed stock; that stock topped up with the day's carry lists; the lists alone; every part that fits
for the day's appliance types; and a balanced truck that keeps a part only while it earns its space.
A share of calls is unplanned: added or moved the same day, so no night-before list saw them.
The dollar figures and that share are made up and stated once, here, so a real run swaps in real ones.
"""
import random
from collections import Counter, defaultdict

from staple.backtest import test_jobs
from staple.predict import Predictor
from staple.world import travel_minutes

REPEAT_VISIT = 165.0   # a second truck roll because a part was missing (made up)
MOVE = 2.0             # putting one part on a truck at the centre overnight, or taking one off (made up)
HOLDING_RATE = 0.25    # yearly cost of money tied up in truck stock, as a share of its value (made up)
UNPLANNED = 0.15       # share of calls added or moved the same day (made up until the dispatch replay measures it)
NEARBY_MINUTES = 30    # another truck is nearby when it is based within this drive of the job
LEAN_TOLERANCE = 2.0   # dollars a truck-day: the shortest list costing within this of the cheapest is recommended
PERMANENT = 0.9        # a part on board at least this share of a truck's days is, in effect, permanent stock
BACKORDER_FLAG = 0.10  # flag a booking when its odds of needing a part no truck nearby holds reach this (made up)
KS = range(1, 9)
SHOWN_K = 3
TODAY, EVERYTHING, BALANCED = "today's trucks", "every part that fits for the day's types", "balanced truck"
TOP_UP, LISTS_ONLY = "today's stock plus the day's lists", "the day's lists only"
CONTROL, CONTROL_BALANCED = "top-up from shuffled history", "balanced from shuffled history"
MARKS = ("needs a certified technician", "on this truck", "from a centre overnight", "on another truck nearby",
         "back-ordered")   # in the order mark() tries them: a part gets the first that holds


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

    def holding(self, part):
        return self.cost[part] * HOLDING_RATE / 365


def mark_unplanned(jobs, seed=2026):
    rng = random.Random(seed)
    for j in jobs:
        j["unplanned"] = rng.random() < UNPLANNED


def ranked_lists(predictor, jobs):
    """Every part for each job, likeliest first, in the predictor's own carry-list order."""
    out = {}
    for j in jobs:
        odds = predictor.odds(j["type"], j["family"], j["problem"])
        out[j["job"]] = sorted(odds.items(), key=lambda kv: (-kv[1], kv[0]))
    return out


def surprise_value(conn, predictor):
    """Per truck and part, the repeat-visit dollars a day that unplanned calls put on carrying it, from training."""
    days = dict(conn.execute("SELECT tech, COUNT(DISTINCT day) FROM jobs WHERE split = 'train' GROUP BY tech"))
    value = defaultdict(dict)
    for tech, appliance, calls in conn.execute(
            "SELECT tech, type, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY tech, type"):
        n, counts = predictor.history("type", appliance)
        for part in predictor.parts_of[appliance]:
            value[tech][part] = UNPLANNED * calls / days[tech] * counts[part] / n * REPEAT_VISIT
    return value


def fill(fleet, tech, load, wanted):
    """Add the wanted parts, best first, while the truck has space and may carry them."""
    space = sum(fleet.size[p] for p in load)
    for part in sorted(wanted, key=lambda q: (-wanted[q], q)):
        if part not in load and fleet.may_carry(tech, part) and space + fleet.size[part] <= fleet.capacity[tech]:
            load.add(part)
            space += fleet.size[part]
    return load


def list_load(fleet, tech, day_jobs, lists, k, keep_stock):
    """Today's stock if kept, then the booked calls' carry-list parts, likeliest first, while space allows."""
    # ponytail: a truck that holds a part is taken to hold enough of it, as the made-up history assumes;
    # count units per truck-day once real usage shows how often two calls on one route need the same part.
    wanted = {}
    for j in day_jobs:
        if not j["unplanned"]:
            for part, p in lists[j["job"]][:k]:
                wanted[part] = max(wanted.get(part, 0.0), p)
    return fill(fleet, tech, set(fleet.stock[tech]) if keep_stock else set(), wanted)


def balanced_load(fleet, tech, day_jobs, lists, surprise, before):
    """Each part's worth for tomorrow, per unit of space: the booked calls' odds and the unplanned calls'
    share, times a repeat visit, less its holding, and less a move unless it is already on board."""
    # ponytail: worth adds each call's odds as if one part fixed it; a call needing two parts is counted
    # generously. A per-call model of complete part sets is the upgrade if real data shows it matters.
    worth = Counter(surprise[tech])
    for j in day_jobs:
        if not j["unplanned"]:
            for part, p in lists[j["job"]]:
                worth[part] += p * REPEAT_VISIT
    net = {p: w - fleet.holding(p) + (MOVE if p in before else -MOVE) for p, w in worth.items()}
    return fill(fleet, tech, set(), {p: v / fleet.size[p] for p, v in net.items() if v > 0})


def today(fleet):
    return lambda tech, day_jobs, before: set(fleet.stock[tech])


def top_up(fleet, lists, k, keep_stock=True):
    return lambda tech, day_jobs, before: list_load(fleet, tech, day_jobs, lists, k, keep_stock)


def balanced(fleet, lists, surprise):
    return lambda tech, day_jobs, before: balanced_load(fleet, tech, day_jobs, lists, surprise, before)


def policies(fleet, lists, shuffled, surprise, every):
    out = [(TODAY, 0, today(fleet)), (EVERYTHING, every, top_up(fleet, lists, every)),
           (BALANCED, 0, balanced(fleet, lists, surprise)), (CONTROL_BALANCED, 0, balanced(fleet, shuffled, surprise))]
    for k in KS:
        out += [(TOP_UP, k, top_up(fleet, lists, k)), (LISTS_ONLY, k, top_up(fleet, lists, k, keep_stock=False)),
                (CONTROL, k, top_up(fleet, shuffled, k))]
    return out


def truck_days(jobs):
    days = defaultdict(list)
    for j in jobs:
        days[(j["day"], j["tech"])].append(j)
    return days


def day_cost(fleet, load, before, misses):
    """One truck-day in dollars: repeat visits, parts moved on or off overnight, and money tied up in stock."""
    return misses * REPEAT_VISIT, len(load ^ before) * MOVE, sum(fleet.holding(p) for p in load)


def replay(fleet, jobs, chosen, record=False):
    """Every held-back truck-day under each policy, each truck in date order, averaged per truck-day."""
    days = truck_days(jobs)
    order = sorted(days, key=lambda d: (d[1], d[0]))
    worked = Counter(tech for _, tech in days)
    rows = []
    for name, k, build in chosen:
        t, last, nights, loads = defaultdict(float), {}, defaultdict(Counter), {}
        for day, tech in order:
            day_jobs, before = days[(day, tech)], last.get(tech, fleet.stock[tech])
            load = build(tech, day_jobs, before)
            covered = sum(1 for j in day_jobs if j["needed"] <= load)
            repeat, handling, holding = day_cost(fleet, load, before, len(day_jobs) - covered)
            for key, v in (("jobs", len(day_jobs)), ("covered", covered), ("parts", len(load)),
                           ("space", sum(fleet.size[p] for p in load)), ("value", sum(fleet.cost[p] for p in load)),
                           ("moves", len(load ^ before)), ("repeat", repeat), ("handling", handling),
                           ("holding", holding)):
                t[key] += v
            last[tech] = load
            nights[tech].update(load)
            if record:
                loads[(day, tech)] = load
        n = len(days)
        row = {"policy": name, "k": k, "truck_days": n, "coverage": t["covered"] / t["jobs"],
               **{key: t[key] / n for key in ("jobs", "parts", "space", "value", "moves",
                                              "repeat", "handling", "holding")}}
        row["total"] = row["repeat"] + row["handling"] + row["holding"]
        # Inventory ROI here: repeat-visit dollars a year that the stock avoids, per dollar of stock on the truck.
        row["roi"] = t["covered"] / n * REPEAT_VISIT * 365 / row["value"]
        row["permanent"] = sum(sum(1 for c in nights[tech].values() if c >= PERMANENT * worked[tech])
                               for tech in worked) / len(worked)
        if record:
            row["loads"] = loads
        rows.append(row)
    return rows


def mark(fleet, tech, zone, part):
    """Where one carry-list part would come from for this job's technician: the first of MARKS that holds."""
    holds = {"needs a certified technician": lambda: part in fleet.locked and tech not in fleet.certified,
             "on this truck": lambda: part in fleet.stock[tech],
             "from a centre overnight": lambda: part not in fleet.eta,
             "on another truck nearby": lambda: any(o != tech and part in s and travel_minutes(fleet.home[o], zone)
                                                    <= NEARBY_MINUTES for o, s in fleet.stock.items()),
             "back-ordered": lambda: True}
    return next(m for m in MARKS if holds[m]())


def availability(fleet, jobs, lists, k=SHOWN_K):
    """The marks on every carry-list part, and a back-order flag: the job's odds of needing a part no
    truck nearby holds. The odds summed over all jobs should match how many were actually blocked."""
    counts, cache = defaultdict(int), {}
    flagged = blocked = caught = 0
    expected = 0.0
    for j in jobs:
        def where(p):
            key = (j["tech"], j["zone"], p)
            if key not in cache:
                cache[key] = mark(fleet, j["tech"], j["zone"], p)
            return cache[key]
        ranked = lists[j["job"]]
        for p, _ in ranked[:k]:
            counts[where(p)] += 1
        stuck = sum(o for p, o in ranked if where(p) == "back-ordered")
        block = any(where(p) == "back-ordered" for p in j["needed"])
        expected += stuck
        flagged += stuck >= BACKORDER_FLAG
        blocked += block
        caught += stuck >= BACKORDER_FLAG and block
    total = sum(counts.values())
    return {"marks": {m: counts[m] / total for m in MARKS}, "jobs": len(jobs), "expected": expected,
            "flagged": flagged, "blocked": blocked, "caught": caught}


def lean(rows, policy):
    """The shortest carry list whose daily cost is within LEAN_TOLERANCE of the policy's cheapest."""
    mine = sorted((r for r in rows if r["policy"] == policy), key=lambda r: r["k"])
    cheapest = min(r["total"] for r in mine)
    return next(r for r in mine if r["total"] <= cheapest + LEAN_TOLERANCE)


def run(conn):
    """Every load policy over the whole held-back period, written to load_policy and availability. It takes
    no job list, so no caller can leave those tables holding a subset; the marked jobs come back as "jobs"."""
    jobs = test_jobs(conn)
    mark_unplanned(jobs)
    fleet, real = Fleet(conn), Predictor(conn)
    lists, surprise = ranked_lists(real, jobs), surprise_value(conn, real)
    shuffled = ranked_lists(Predictor(conn, shuffled=True, seed=7), jobs)
    every = max(len(v) for v in lists.values())
    rows = replay(fleet, jobs, policies(fleet, lists, shuffled, surprise, every))
    avail = availability(fleet, jobs, lists)

    conn.execute("DELETE FROM load_policy")
    conn.execute("DELETE FROM availability")
    conn.executemany("INSERT INTO load_policy VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(r["policy"], r["k"], r["truck_days"], r["jobs"], r["coverage"], r["parts"], r["space"],
                       r["value"], r["moves"], r["permanent"], r["repeat"], r["handling"], r["holding"],
                       r["total"], r["roi"]) for r in rows])
    conn.executemany("INSERT INTO availability VALUES (?,?,?)",
                     [("mark", m, s) for m, s in avail["marks"].items()] +
                     [("jobs", m, avail[m]) for m in ("jobs", "expected", "flagged", "blocked", "caught")])
    conn.commit()
    named = {(r["policy"], r["k"]): r for r in rows}
    best = {p: lean(rows, p) for p in (TOP_UP, LISTS_ONLY)}
    now, top = named[(TODAY, 0)], best[TOP_UP]
    # The repeat-visit cost at which the top-up stops paying: its extra handling and holding over the misses it saves.
    break_even = ((top["handling"] + top["holding"] - now["handling"] - now["holding"]) /
                  ((now["repeat"] - top["repeat"]) / REPEAT_VISIT))
    return {"rows": rows, "best": best, "today": now, "everything": named[(EVERYTHING, every)],
            "balanced": named[(BALANCED, 0)], "control_balanced": named[(CONTROL_BALANCED, 0)],
            "break_even": break_even, "availability": avail, "fleet": fleet, "lists": lists, "jobs": jobs,
            "shuffled": shuffled, "surprise": surprise}
