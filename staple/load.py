"""Truck loads, dollars and availability: what each truck should hold for tomorrow's route.

Centers restock trucks between workdays, so tomorrow's load can be built from tomorrow's booked calls.
Each held-back truck-day is replayed, truck by truck in date order, under several policies: today's
fixed stock; that stock topped up with the day's carry lists; the lists alone; every part that fits
for the day's appliance types; and a balanced truck that keeps a part only while it earns its space.
A share of calls is unplanned: added or moved the same day, so no night-before list saw them.
The dollar figures and that share are made up and stated once, here, so a real run swaps in real ones.

The balanced truck is also replayed three ways that test its gain: loaded from shuffled history, held to
the space today's stock uses, and with one move priced higher than the made-up figure.
"""
import datetime as dt
import random
from collections import Counter, defaultdict

from staple.backtest import test_jobs
from staple.predict import Predictor
from staple.world import travel_minutes

REPEAT_VISIT = 165.0   # a repeat visit because a part was missing (made up)
MOVE = 2.0             # putting one part on a truck at the center overnight, or taking one off (made up)
MOVE_STEPS = (5.0, 10.0, 20.0)   # higher prices for one move, replayed to show how far the saving leans on MOVE
HOLDING_RATE = 0.25    # yearly cost of money tied up in truck stock, as a share of its value (made up)
UNPLANNED_SEED = 2026  # seeds which held-back calls are marked unplanned
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
EQUAL_SPACE = "balanced truck, held to the space today's stock uses"
MARKS = ("needs a certified technician", "aboard", "from a center overnight", "on a truck nearby",
         "back-ordered")   # in the order mark() tries them: a part gets the first that holds


class Fleet:
    """The trucks as they stand today: what each holds, its space, who drives it, and the centers' back-orders."""

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
        self.backorders = conn.execute("SELECT part, starts, ships FROM backorders").fetchall()
        self._stuck = {}

    def stuck(self, day):
        """The parts no center can ship on this day, each with the days until it ships."""
        if day not in self._stuck:
            today = dt.date.fromisoformat(day)
            self._stuck[day] = {part: (dt.date.fromisoformat(ships) - today).days
                                for part, starts, ships in self.backorders if starts <= day < ships}
        return self._stuck[day]

    def may_carry(self, tech, part, stuck=(), aboard=()):
        """A locked part rides only with a certified technician. A back-ordered part cannot be loaded, so it rides
        only where it was aboard the night before."""
        if part in self.locked and tech not in self.certified:
            return False
        return part not in stuck or part in aboard

    def holding(self, part):
        return self.cost[part] * HOLDING_RATE / 365

    def used(self, tech):
        """The space today's stock takes on this technician's truck."""
        return sum(self.size[p] for p in self.stock[tech])


def at_move(move):
    """The balanced truck's row when one move is priced at this many dollars."""
    return f"balanced truck, a move priced at ${move:.0f}"


def mark_unplanned(jobs, seed=UNPLANNED_SEED):
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


def fill(fleet, tech, load, wanted, stuck=(), before=(), room=None):
    """Add the wanted parts, best first, while the truck has space and may carry them. A back-ordered part that
    was aboard the night before stays aboard first: the center has none to swap it for, and a part sent back
    would be one the center could ship. room, when given, is a tighter space limit than the truck's own."""
    room = fleet.capacity[tech] if room is None else room
    load |= {p for p in before if p in stuck}
    space = sum(fleet.size[p] for p in load)
    for part in sorted(wanted, key=lambda q: (-wanted[q], q)):
        if (part not in load and fleet.may_carry(tech, part, stuck, before)
                and space + fleet.size[part] <= room):
            load.add(part)
            space += fleet.size[part]
    return load


def list_load(fleet, tech, day_jobs, lists, k, keep_stock, before=(), stuck=()):
    """Today's stock if kept, then the booked calls' carry-list parts, likeliest first, while space allows."""
    # ponytail: a truck that holds a part is taken to hold enough of it, as the made-up history assumes. The
    # upgrade is to count units per truck-day, once real usage shows how often two calls on one route need one part.
    wanted = {}
    for j in day_jobs:
        if not j["unplanned"]:
            for part, p in lists[j["job"]][:k]:
                wanted[part] = max(wanted.get(part, 0.0), p)
    return fill(fleet, tech, set(fleet.stock[tech]) if keep_stock else set(), wanted, stuck, before)


def worth(fleet, tech, day_jobs, lists, surprise, before, move=MOVE):
    """Each part's worth for tomorrow in dollars: the booked calls' odds and the unplanned calls' share,
    times a repeat visit, less its holding, and less a move unless it is already on board."""
    # ponytail: worth adds each call's odds as if one part fixed it; a call needing two parts is counted
    # generously. A per-call model of complete part sets is the upgrade if real data shows it matters.
    gross = Counter(surprise[tech])
    for j in day_jobs:
        if not j["unplanned"]:
            for part, p in lists[j["job"]]:
                gross[part] += p * REPEAT_VISIT
    return {p: w - fleet.holding(p) + (move if p in before else -move) for p, w in gross.items()}


def balanced_load(fleet, tech, day_jobs, lists, surprise, before, stuck=(), room=None, move=MOVE):
    """The parts worth carrying, best worth per unit of space first, while the truck has room."""
    net = worth(fleet, tech, day_jobs, lists, surprise, before, move)
    return fill(fleet, tech, set(), {p: v / fleet.size[p] for p, v in net.items() if v > 0}, stuck, before, room)


# Each policy builds one truck's load for a day from its booked calls, its load the night before, and the
# parts no center can ship that day.
def today(fleet):
    return lambda tech, day_jobs, before, stuck: set(fleet.stock[tech])


def top_up(fleet, lists, k, keep_stock=True):
    return lambda tech, day_jobs, before, stuck: list_load(fleet, tech, day_jobs, lists, k, keep_stock, before, stuck)


def balanced(fleet, lists, surprise, equal_space=False, move=MOVE):
    """equal_space holds each truck to the space its own stock uses today, so the balanced truck and today's
    truck are compared on the same room. move is the price of one move the truck plans by."""
    return lambda tech, day_jobs, before, stuck: balanced_load(
        fleet, tech, day_jobs, lists, surprise, before, stuck, fleet.used(tech) if equal_space else None, move)


def policies(fleet, lists, shuffled, surprise, every):
    out = [(TODAY, 0, today(fleet)), (EVERYTHING, every, top_up(fleet, lists, every)),
           (BALANCED, 0, balanced(fleet, lists, surprise)), (CONTROL_BALANCED, 0, balanced(fleet, shuffled, surprise)),
           (EQUAL_SPACE, 0, balanced(fleet, lists, surprise, equal_space=True))]
    for k in KS:
        out += [(TOP_UP, k, top_up(fleet, lists, k)), (LISTS_ONLY, k, top_up(fleet, lists, k, keep_stock=False)),
                (CONTROL, k, top_up(fleet, shuffled, k))]
    return out


def truck_days(jobs):
    days = defaultdict(list)
    for j in jobs:
        days[(j["day"], j["tech"])].append(j)
    return days


def day_cost(fleet, load, before, misses, move=MOVE):
    """One truck-day in dollars: repeat visits, parts moved on or off overnight, and money tied up in stock."""
    return misses * REPEAT_VISIT, len(load ^ before) * move, sum(fleet.holding(p) for p in load)


def replay(fleet, jobs, chosen, record=False, move=MOVE):
    """Every held-back truck-day under each policy, each truck in date order, averaged per truck-day. move is
    what one move is charged at; a policy that plans by another price is being scored on the wrong bill."""
    days = truck_days(jobs)
    order = sorted(days, key=lambda d: (d[1], d[0]))
    worked = Counter(tech for _, tech in days)
    rows = []
    for name, k, build in chosen:
        t, last, nights, loads = defaultdict(float), {}, defaultdict(Counter), {}
        for day, tech in order:
            day_jobs, before = days[(day, tech)], last.get(tech, fleet.stock[tech])
            load = build(tech, day_jobs, before, fleet.stuck(day))
            covered = sum(1 for j in day_jobs if j["needed"] <= load)
            # A job that needed no part is covered by an empty truck, so the stock saved no repeat visit on it.
            saved = sum(1 for j in day_jobs if j["needed"] and j["needed"] <= load)
            repeat, handling, holding = day_cost(fleet, load, before, len(day_jobs) - covered, move)
            for key, v in (("jobs", len(day_jobs)), ("covered", covered), ("saved", saved), ("parts", len(load)),
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
               **{key: t[key] / n for key in ("jobs", "saved", "parts", "space", "value", "moves",
                                              "repeat", "handling", "holding")}}
        row["total"] = row["repeat"] + row["handling"] + row["holding"]
        # Inventory ROI here: repeat-visit dollars a year that the stock avoids, per dollar of stock on the truck.
        # Only a job that needed a part counts, because an empty truck covers the rest.
        row["roi"] = row["saved"] * REPEAT_VISIT * 365 / row["value"]
        row["permanent"] = sum(sum(1 for c in nights[tech].values() if c >= PERMANENT * worked[tech])
                               for tech in worked) / len(worked)
        if record:
            row["loads"] = loads
        rows.append(row)
    return rows


def mark(fleet, tech, zone, part, stuck=()):
    """Where one carry-list part would come from for this job's technician: the first of MARKS that holds. stuck
    is the parts no center can ship on the job's day."""
    holds = {"needs a certified technician": lambda: part in fleet.locked and tech not in fleet.certified,
             "aboard": lambda: part in fleet.stock[tech],
             "from a center overnight": lambda: part not in stuck,
             "on a truck nearby": lambda: any(o != tech and part in s and travel_minutes(fleet.home[o], zone)
                                                    <= NEARBY_MINUTES for o, s in fleet.stock.items()),
             "back-ordered": lambda: True}
    return next(m for m in MARKS if holds[m]())


def backorder_flag(ranked, where):
    """A booking's odds of needing a part no truck nearby holds, summed over its parts marked back-ordered, and
    whether they reach the flag."""
    stuck = sum(o for p, o in ranked if where(p) == "back-ordered")
    return stuck, stuck >= BACKORDER_FLAG


def availability(fleet, jobs, lists, k=SHOWN_K):
    """The marks on every carry-list part, and a back-order flag: the job's odds of needing a part no
    truck nearby holds. The odds summed over all jobs should match how many were actually blocked."""
    counts, cache = defaultdict(int), {}
    flagged = blocked = caught = 0
    expected = 0.0
    for j in jobs:
        unshippable = fleet.stuck(j["day"])

        def where(p):
            key = (j["tech"], j["zone"], p, p in unshippable)
            if key not in cache:
                cache[key] = mark(fleet, j["tech"], j["zone"], p, unshippable)
            return cache[key]
        ranked = lists[j["job"]]
        for p, _ in ranked[:k]:
            counts[where(p)] += 1
        stuck, flag = backorder_flag(ranked, where)
        block = any(where(p) == "back-ordered" for p in j["needed"])
        expected += stuck
        flagged += flag
        blocked += block
        caught += flag and block
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
    shuffled = ranked_lists(Predictor(conn, shuffled=True), jobs)
    every = max(len(v) for v in lists.values())
    rows = replay(fleet, jobs, policies(fleet, lists, shuffled, surprise, every))
    for move in MOVE_STEPS:
        rows += replay(fleet, jobs, [(at_move(move), 0, balanced(fleet, lists, surprise, move=move))], move=move)
    avail = availability(fleet, jobs, lists)

    conn.execute("DELETE FROM load_policy")
    conn.execute("DELETE FROM availability")
    conn.executemany("INSERT INTO load_policy VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(r["policy"], r["k"], r["truck_days"], r["jobs"], r["coverage"], r["saved"], r["parts"], r["space"],
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
            "equal_space": named[(EQUAL_SPACE, 0)], "moves": {m: named[(at_move(m), 0)] for m in MOVE_STEPS},
            "break_even": break_even, "availability": avail, "fleet": fleet, "lists": lists, "jobs": jobs,
            "shuffled": shuffled, "surprise": surprise}
