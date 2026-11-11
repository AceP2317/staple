"""The buyer's choice on each back-ordered part: four actions priced in expected dollars, and any decider's
choices scored against the cheapest.

Every held-back day lists the parts no center can ship. For each one the buyer takes one of four actions: send
the calls anyway, pay to expedite the part, move one from a truck unlikely to need it, or rebook the calls
already waiting on it past the ship day. The engine prices all four from training only, so a price is a
forecast and never the held-back days' own outcome. A decider is anything that names an action for a case. It
is charged the price of the action it named, and its score is the dollars that costs over the cheapest action.
An action that cannot be carried out is charged as sending anyway, and counted. The fee, the days an expedited
part takes and the cost of a day's wait are made up and stated once, here.
"""
import random
from collections import defaultdict

from staple import backorders
from staple.backtest import test_jobs
from staple.load import BACKORDER_FLAG, MOVE, REPEAT_VISIT, Fleet, mark, mark_unplanned, ranked_lists
from staple.predict import Predictor

EXPEDITE = 95.0       # the fee to have a back-ordered part shipped early (made up)
EXPEDITE_DAYS = 2     # an expedited part ships in this many days (made up)
WAIT_DAY = 10.0       # what one more day of waiting costs when a booked call is moved to a later day (made up)
SEND, RUSH, SHIFT, REBOOK = ACTIONS = ("send anyway", "expedite", "move one", "rebook")
ARITHMETIC = "the cheapest action, by the engine's arithmetic"
RULES = ((ARITHMETIC, lambda case: cheapest(prices(case))),
         ("send anyway every time", lambda case: SEND),
         ("expedite every time", lambda case: RUSH))
CONTROL = "the cheapest action for another case's facts (control)"
SHUFFLE_SEED = 2026   # seeds which other case the control reads


def prices(case, repeat=REPEAT_VISIT, move=MOVE, fee=EXPEDITE, fast=EXPEDITE_DAYS, wait=WAIT_DAY):
    """One case's four actions in expected dollars. An action that cannot be carried out has no price."""
    send, days = case["stuck"] * repeat, case["days_left"]
    waiting = [odds for _, odds in case["waiting"]]
    return {SEND: send,
            # The part ships in `fast` days, so only the calls stuck in those days still risk a repeat visit.
            RUSH: fee + send * min(days, fast) / days,
            # One part comes off a truck and goes on another: two moves, and the calls still stuck after it.
            SHIFT: None if case["move"] is None else 2 * move + case["move"]["stuck"] * repeat,
            # Each waiting call waits until the part ships, and no longer risks a repeat visit for it.
            REBOOK: None if not waiting else (len(waiting) * days * wait
                                              + max(0.0, case["stuck"] - sum(waiting)) * repeat)}


def cheapest(priced):
    """The cheapest action that can be carried out. A tie goes to the earlier action, so doing nothing wins one."""
    return min((a for a in ACTIONS if priced[a] is not None), key=lambda a: priced[a])


def best_move(demand, fleet, part, holders):
    """The one move that leaves the smallest share of calls stuck: a truck that holds the part gives it to a truck
    that holds none and may carry it. None when no truck holds one, or none may take it."""
    # ponytail: the taking truck is assumed to have room for one more part. The upgrade is to price the part it
    # would leave behind, with the truck-load replay.
    takers = [t for t in sorted(fleet.home) if t not in holders and (part not in fleet.locked or t in fleet.certified)]
    appliance = demand.part_type[part]
    moves = [(backorders.stuck_share(demand, fleet, appliance, sorted(set(holders) - {giver} | {taker}))[1], giver, taker)
             for giver in holders for taker in takers]
    return min(moves) if moves else None


def cases(conn):
    """Every back-ordered part of every held-back day as the buyer's case: the list's own forecast, the best
    single move, and the booked calls waiting on the part with their odds of needing it."""
    fleet, demand, jobs = Fleet(conn), backorders.Demand(conn), test_jobs(conn)
    mark_unplanned(jobs)
    lists = ranked_lists(Predictor(conn), jobs)
    waiting = defaultdict(list)
    for j in jobs:
        stuck = fleet.stuck(j["day"])
        if j["unplanned"] or not stuck:
            continue
        for part, odds in lists[j["job"]]:
            if (part in stuck and odds >= BACKORDER_FLAG
                    and mark(fleet, j["tech"], j["zone"], part, stuck) == "back-ordered"):
                waiting[(j["day"], part)].append((j["job"], odds))
    moves, out = {}, []
    for day in sorted({j["day"] for j in jobs}):
        for r in backorders.forecast(demand, fleet, day):
            part = r["part"]
            if part not in moves:     # the trucks' stock is fixed, so a part's best move is the same every day
                moves[part] = best_move(demand, fleet, part, r["holders"])
            move = moves[part] and {"stuck": r["expected"] * moves[part][0], "giver": moves[part][1],
                                    "taker": moves[part][2]}
            out.append({**r, "move": move, "waiting": waiting[(day, part)]})
    return out


def score(cases, choose, **dollars):
    """A decider's choices over the cases: what they cost, the dollars that is over the cheapest action, and how
    many could not be carried out. choose takes a case and names an action."""
    cost = lost = 0.0
    invalid = 0
    for case in cases:
        priced = prices(case, **dollars)
        action = choose(case)
        if priced.get(action) is None:      # not one of the four, or one that cannot be carried out here
            invalid += 1
            action = SEND
        cost += priced[action]
        lost += priced[action] - priced[cheapest(priced)]
    return {"decisions": len(cases), "invalid": invalid, "cost": cost, "lost": lost}


def other_case(count, seed=SHUFFLE_SEED):
    """For each case, another case to read: the cases stand in one seeded ring and each reads the next, so none
    reads itself."""
    ring = list(range(count))
    random.Random(seed).shuffle(ring)
    return dict(zip(ring, ring[1:] + ring[:1]))


def shuffled(cases, seed=SHUFFLE_SEED):
    """The control decider: the cheapest action for another case's facts, so its choice no longer rests on the
    case it is made for."""
    other = other_case(len(cases), seed)
    borrowed = {(c["day"], c["part"]): cheapest(prices(cases[other[i]])) for i, c in enumerate(cases)}
    return lambda case: borrowed[(case["day"], case["part"])]


def run(conn):
    """Every case priced and every built-in decider scored, written to buyer_prices and buyer_score. It takes no
    case list, so no caller can leave the tables holding a subset."""
    found = cases(conn)
    scores = {name: score(found, choose) for name, choose in RULES + ((CONTROL, shuffled(found)),)}
    conn.execute("DELETE FROM buyer_prices")
    conn.execute("DELETE FROM buyer_score")
    for c in found:
        priced = prices(c)
        conn.execute("INSERT INTO buyer_prices VALUES (?,?,?,?,?,?,?,?)",
                     (c["day"], c["part"], len(c["waiting"]), *(priced[a] for a in ACTIONS), cheapest(priced)))
    conn.executemany("INSERT INTO buyer_score VALUES (?,?,?,?,?)",
                     [(name, s["decisions"], s["invalid"], s["cost"], s["lost"]) for name, s in scores.items()])
    conn.commit()
    return {"cases": found, "scores": scores}
