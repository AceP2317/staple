"""The week's plan: the most recent held-back days, coded by Claude from each customer's own words and replayed
call by call, shaped into what a parts room, a call center, dispatch and a buyer act on before 08:00.

Nothing here plans anything. Every route, load and move is what the dispatch replay decided, recorded as it
decided it. The 92-day scorecard runs on the booked codes; this week runs on Claude's readings, asking its
saved follow-up question where it was unsure. Both of the week's replays are seeded so its sick calls and
breakdowns are the ones the 92-day run met on the same days.
"""
from collections import defaultdict

from staple import agent, buyer, dispatch, load, metrics, triage
from staple.backtest import test_jobs
from staple.predict import Predictor
from staple.world import ZONES, part_name, travel_minutes

WEEK = 7
LIKELY = load.BACKORDER_FLAG     # a part a call has at least this chance of needing counts as a likely part
BUYER_ITEMS = 3                  # the buyer's list shows this many back-orders a day, costliest first
BEHIND = 0.05                    # a call this likely to need a part is named as a reason the part rides
WHERE = ("needs a certified technician", "aboard", "on a truck nearby", "at a center, not loaded", "back-ordered")
# How the buyer's list words each of the four actions.
BUYER_DOES = {buyer.SEND: "Send the calls anyway", buyer.RUSH: "Expedite", buyer.SHIFT: "Move one",
              buyer.REBOOK: "Rebook the waiting calls"}


def clock(minutes):
    """Minutes after 08:00 as a time of day."""
    m = int(round(minutes)) + 8 * 60
    return f"{m // 60:02d}:{m % 60:02d}"


def week_jobs(conn):
    """The most recent WEEK held-back days, marked unplanned over the whole period exactly as the 92-day run
    marks them, and the index of the first of those days among all held-back days."""
    jobs = test_jobs(conn)
    load.mark_unplanned(jobs)
    days = sorted({j["day"] for j in jobs})
    first = len(days) - WEEK
    keep = set(days[first:])
    return [j for j in jobs if j["day"] in keep], first


def recode(jobs, answers, followups):
    """Each job with its problem code replaced by Claude's reading of the customer's words, after the saved
    follow-up question where Claude was unsure, and what Claude did on the way."""
    out = []
    for j in jobs:
        first = answers[j["type"]][j["words"]]
        follow = followups.get(j["type"], {}).get(j["words"])
        asked = first["confidence"] < triage.BLEND_BELOW and follow is not None
        reply = triage.honest(j, follow["answers"]) if asked else None
        code, confidence = triage.asked(first, follow, j, triage.honest)
        out.append(dict(j, problem=f"{j['type']}.{code}", booked=j["problem"], ai={
            "first": first["code"], "first_confidence": first["confidence"], "code": code, "confidence": confidence,
            "question": follow["question"] if asked else None, "offered": follow["answers"] if asked else [],
            "reply": reply}))
    return out


class Context:
    """What every day's shaping reads: the fleet, the crew, the parts odds, and the names behind the codes."""

    def __init__(self, conn, jobs):
        self.conn, self.fleet, self.crew = conn, load.Fleet(conn), dispatch.Crew(conn)
        predictor = Predictor(conn)
        self.lists, self.surprise = load.ranked_lists(predictor, jobs), load.surprise_value(conn, predictor)
        self.first_lists = {j["job"]: sorted(predictor.odds(j["type"], j["family"], f"{j['type']}.{j['ai']['first']}").items(),
                                             key=lambda kv: (-kv[1], kv[0])) for j in jobs if j["ai"]["question"]}
        self.labels = dict(conn.execute("SELECT code, label FROM problems"))
        self.names = {part: part_name(part) for (part,) in conn.execute("SELECT part FROM parts")}
        self.kind = dict(conn.execute("SELECT part, type FROM parts"))
        self.truck = dict(conn.execute("SELECT tech, truck FROM trucks"))
        self.techs = {t: {"level": lv, "profile": pr, "home": z, "certified": bool(c), "truck": self.truck[t]}
                      for t, lv, pr, z, c in conn.execute("SELECT tech, level, profile, home_zone, certified FROM techs")}
        # The buyer's list is the back-order query's own rows, so the plan and the evidence quote one figure.
        self.backorders = defaultdict(list)
        self.flags_right = metrics.read(conn, "backorder_flag")[0]["flags_right_pct"]
        for row in metrics.read(conn, "backorder_exposure"):
            self.backorders[row["day"]].append(row)
        if not self.backorders:
            raise RuntimeError("the back-order list is empty: backorders.run(conn) writes it before the week's plan is built")
        # The buyer's prices and the leading model's decisions, so the list shows what the scorer scored.
        self.priced = {(day, part): (dict(zip(buyer.ACTIONS, priced)), cheapest, giver, taker)
                       for day, part, *priced, cheapest, giver, taker in conn.execute(
                           "SELECT day, part, send_anyway, expedite, move_one, rebook, cheapest, giver, taker FROM buyer_prices")}
        if not self.priced:
            raise RuntimeError("the buyer's prices are missing: buyer.run(conn) writes them before the week's plan is built")
        self.buyer = agent.leading(conn)

    def where(self, loads, tech, zone, part, stuck):
        """Where one carry-list part stands once tonight's trucks are loaded: the first of WHERE that holds. stuck
        is the parts no center can ship that day."""
        f = self.fleet
        if part in f.locked and tech not in f.certified:
            return WHERE[0]
        if part in loads[tech]:
            return WHERE[1]
        if any(o != tech and part in s and travel_minutes(f.home[o], zone) <= load.NEARBY_MINUTES for o, s in loads.items()):
            return WHERE[2]
        return WHERE[3] if part not in stuck else WHERE[4]

    def chance(self, job, tech, cargo):
        return first_visit_chance(self.lists, job, cargo, self.crew.belief[(tech, job["type"])][0])


def first_visit_chance(lists, job, cargo, rate):
    """The plan-time chance of a first-time fix: the truck holds what the call needs, times the technician's
    success rate on the appliance as the history shows it."""
    return (1 - dispatch.miss_odds(lists, job, lambda p: p in cargo)) * rate


def tally(rec, jobs, crew):
    """A day's figures from its recorded visits, counted the way the replay counts them."""
    t, zone = defaultdict(float), {}
    for _, tech, job, start, end, covered in rec["visits"]:
        t["served"] += 1
        t["fixes"] += covered * crew.skill[(tech, jobs[job]["type"])][0]
        t["missing"] += not covered
        t["drive"] += travel_minutes(zone.get(tech, crew.home[tech]), jobs[job]["zone"])
        t["repair"] += end - start
        zone[tech] = jobs[job]["zone"]
    t["calls"], t["rolled"] = len(jobs), len(rec["rolled"])
    t["cost"] = (t["drive"] + t["repair"]) * dispatch.MINUTE + (t["served"] - t["fixes"]) * load.REPEAT_VISIT
    return dict(t)


def call(ctx, rec, job, tech, start, end):
    """One call as the plan shows it: who, when, the customer's words, what Claude read, and the parts."""
    cargo = rec["load"][tech]
    ranked = ctx.lists[job["job"]]
    placed = rec["placed"].get(job["job"])
    return {"job": job["job"], "tech": tech, "start": start, "end": end, "zone": job["zone"], "type": job["type"],
            "family": job["family"], "words": job["words"], "problem": ctx.labels[job["problem"]],
            "booked": ctx.labels[job["booked"]], "ai": job["ai"], "priority": dispatch.priority(job, {}),
            "chance": ctx.chance(job, tech, cargo),
            "parts": [{"part": p, "name": ctx.names[p], "odds": o,
                       "where": ctx.where(rec["load"], tech, job["zone"], p, rec["stuck"])}
                      for p, o in ranked[:load.SHOWN_K]],
            "first_parts": [ctx.names[p] for p, _ in ctx.first_lists.get(job["job"], [])[:load.SHOWN_K]],
            "runner": placed and placed[2], "gap": placed and placed[3] is not None and placed[3] - placed[1]}


def sheet(ctx, rec, tech, route):
    """Tonight's load for one truck: each part put on, taken off or kept, with its worth and the calls behind it."""
    cargo, before = rec["load"][tech], rec["before"][tech]
    worth = load.worth(ctx.fleet, tech, route, ctx.lists, ctx.surprise, before)
    behind = defaultdict(list)
    for j in route:
        for part, p in ctx.lists[j["job"]]:
            if p >= BEHIND:
                behind[part].append((j["job"], p))
    rows = []
    for part in sorted(cargo | before, key=lambda p: (-worth.get(p, 0.0), p)):
        status = "keep" if part in cargo and part in before else "put on" if part in cargo else "take off"
        rows.append({"part": part, "name": ctx.names[part], "status": status, "worth": worth.get(part, 0.0),
                     "cost": ctx.fleet.cost[part], "size": ctx.fleet.size[part], "spare": ctx.surprise[tech].get(part, 0.0),
                     "calls": sorted(behind[part], key=lambda c: -c[1])[:3]})
    return {"rows": rows, "space": sum(ctx.fleet.size[p] for p in cargo), "capacity": ctx.fleet.capacity[tech],
            "value": sum(ctx.fleet.cost[p] for p in cargo), "worth": worth}


def todo(ctx, day, rec, trucks, jobs):
    """What people do before 08:00, by owner, each item naming the truck or call it concerns. Only booked calls
    appear: an unplanned call has not come in yet when these lists are read."""
    items = []
    for tech in sorted(trucks, key=lambda t: -sum(r["worth"] for r in trucks[t]["sheet"]["rows"] if r["status"] == "put on")):
        rows = trucks[tech]["sheet"]["rows"]
        on, off = [r for r in rows if r["status"] == "put on"], [r for r in rows if r["status"] == "take off"]
        if on or off:
            items.append({"owner": "Parts room", "when": "tonight", "tech": tech, "job": None,
                          "text": f"Truck {ctx.truck[tech]} ({tech}): put on {len(on)}, take off {len(off)}",
                          "detail": "Put on: " + (", ".join(r["name"] for r in on) or "nothing") + ". Take off: "
                                    + (", ".join(r["name"] for r in off) or "nothing") + "."})
    booked = [j for j in sorted(jobs.values(), key=lambda j: j["job"]) if not j["unplanned"]]
    for j in booked:
        a = j["ai"]
        if a["question"] and not a["reply"]:
            items.append({"owner": "Call center", "when": "before 08:00", "tech": None, "job": j["job"],
                          "text": f"Call back about call {j['job']}: Claude's question did not settle it",
                          "detail": f"The customer said \"{j['words']}\". Claude asked \"{a['question']}\" and none of its "
                                    f"answers fit, so the plan runs on its best guess, {ctx.labels[j['problem']]}, at "
                                    f"{a['confidence']:.0%}."})
    for tech, truck in trucks.items():
        for c in truck["calls"]:
            stuck = [p for p in c["parts"] if p["odds"] >= LIKELY and p["where"] == WHERE[4]]
            if stuck:
                p = stuck[0]
                items.append({"owner": "Dispatch", "when": "before 08:00", "tech": tech, "job": c["job"],
                              "text": f"Warn the customer on call {c['job']}: a likely part is back-ordered",
                              "detail": f"{p['name']} ({p['odds']:.0%} likely) ships in {days(rec['stuck'][p['part']])} and no "
                                        f"truck nearby holds one. Offer a later slot, or keep the visit to diagnose. In the back-test "
                                        f"{ctx.flags_right:.0f}% of flagged bookings were stuck, so this is a warning and "
                                        f"not a forecast for this call."})
    for jid in rec["night_rolled"]:
        j = jobs[jid]
        items.append({"owner": "Dispatch", "when": "before 08:00", "tech": None, "job": jid,
                      "text": f"Rebook call {jid}: no technician has room tomorrow",
                      "detail": f"{j['type'].capitalize()}, {ctx.labels[j['problem']]}, priority {dispatch.priority(j, {})}."})
    idle = [tech for tech in sorted(trucks) if not trucks[tech]["calls"]]
    if idle:
        busy = len(trucks) - len(idle)
        items.append({"owner": "Dispatch", "when": "before 08:00", "tech": None, "job": None, "idle": idle,
                      "text": f"{counted(len(idle), 'technician')} {'has' if len(idle) == 1 else 'have'} no booked "
                              f"calls: {', '.join(idle)}",
                      "detail": f"The booked calls fit on {counted(busy, 'route')}. Each call goes to the technician "
                                f"with the lowest expected cost, so the fleet's spare hours gather on a few people "
                                f"and are not spread thin. They are free for the calls that come in during the day and the "
                                f"calls a sick call or a breakdown moves."})
    likely = defaultdict(int)
    for j in booked:
        for part, p in ctx.lists[j["job"]]:
            if p >= LIKELY:
                likely[part] += 1
    # The day's rows of the back-order query, costliest first: the figures here are the evidence table's own.
    for r in sorted(ctx.backorders[day], key=lambda r: (-r["dollars_at_risk"], r["part"]))[:BUYER_ITEMS]:
        need, said = likely[r["part"]], buyer_says(ctx, day, r)
        items.append({"owner": "Buyer", "when": "this week", "tech": None, "job": None, "row": r, "said": said,
                      "ai": said["action"] == said["chosen"],
                      "text": f"{BUYER_DOES[said['action']]}: {ctx.names[r['part']]}, {r['expected_calls']:.1f} calls "
                              "expected before it ships",
                      "detail": f"Ships in {days(r['ships_in_days'])}; {counted(r['trucks_holding'], 'truck')} "
                                f"{'stocks' if r['trucks_holding'] == 1 else 'stock'} one; "
                                + (f"{counted(r['open_zones'], 'zone')} {'has' if r['open_zones'] == 1 else 'have'} no "
                                   f"holder within {load.NEARBY_MINUTES} minutes. " if r["open_zones"] else
                                   f"Every zone has a holder within {load.NEARBY_MINUTES} minutes. ")
                                + f"${r['dollars_at_risk']:,.0f} of repeat visits at risk. {need if need else 'None'} of "
                                  f"tomorrow's booked calls {'are' if need > 1 else 'is'} likely to need it. {said['words']}"})
    return items


def series(words):
    """Words as a sentence lists them: one, one and two, or one, two and three."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def buyer_says(ctx, day, row):
    """What the buyer's list shows for one back-ordered part: the leading model's saved action, reason and note,
    held against the engine's prices. Where no model is scored, or its action cannot be carried out here, the
    list shows the engine's cheapest action and says so."""
    priced, cheapest, giver, taker = ctx.priced[(day, row["part"])]
    case = {"day": day, "part": row["part"], "days_left": row["ships_in_days"],
            "move": giver and {"giver": giver, "taker": taker}}
    who, answers = ctx.buyer or (None, {})
    a = answers.get(agent.name_of(case), {})
    chosen, at = a.get("action"), f"${priced[cheapest]:,.2f}"
    out = {"who": who, "chosen": chosen, "cheapest": cheapest, "action": cheapest, "reason": "", "note": "",
           "gaps": [], "ran_on": False}
    if who is None:
        return dict(out, words=f"No Claude model's decisions are saved for this prompt, so this is the engine's cheapest "
                               f"action, at {at}.")
    if chosen is None:
        return dict(out, words=f"{who} left this case undecided, so this is the engine's cheapest action, at {at}.")
    if priced[chosen] is None:
        return dict(out, words=f"{who} chose to {chosen}, which cannot be carried out here, so this is the engine's "
                               f"cheapest action, at {at}.")
    (reason, cut), (note, cut_note), gaps = agent.as_written(a["reason"]), agent.as_written(a["note"]), agent.left_out(case, a)
    words = (f"{who} chose this from the case's facts. Its reason: {reason}"
             + (" Its reply ran on into stray markup, which is cut here." if cut or cut_note else "")
             + f" Its note, for a person to send: \"{note}\""
             + (f" The note leaves out {series(gaps)}, so a person writes that in first." if gaps else "")
             + (f" The engine's arithmetic prices this action cheapest, at {at}." if chosen == cheapest else
                f" The engine's arithmetic prices {BUYER_DOES[cheapest].lower()} cheapest, at {at}, which is "
                f"${priced[chosen] - priced[cheapest]:,.2f} under this action."))
    return dict(out, action=chosen, reason=reason, note=note, gaps=gaps, ran_on=cut or cut_note, words=words)


def counted(n, noun):
    """A count with its noun, plural where it should be, and "no" for none."""
    return f"{n if n else 'no'} {noun}{'' if n == 1 else 's'}"


def days(n):
    return counted(n, "day")


def morning(ctx, rec, night_calls, ran_tech, jobs):
    """The day's surprises and every call they moved: from whom, to whom, and the first-time fix chance before and after."""
    moved = []
    for jid, origins in sorted(rec["moved"].items()):
        j, to = jobs[jid], ran_tech.get(jid)
        before = night_calls.get(jid)
        moved.append({"job": jid, "from": origins[0][0], "why": origins[0][1], "via": [o for o, _ in origins[1:]],
                      "to": to, "before": before["chance"] if before else None,
                      "after": ctx.chance(j, to, rec["load"][to]) if to else None,
                      "start": next((s for _, t, i, s, _, _ in rec["visits"] if i == jid), None)})
    broken = {t: at for t, at in rec["broken"].items() if t not in rec["sick"]}
    return {"sick": sorted(rec["sick"]), "broken": broken, "moved": moved,
            "stranded": {t: len(rec["load"][t]) for t in broken},
            "changed": {jid: (dispatch.priority(jobs[jid], {}), p) for jid, p in rec["changed"].items()}}


def day_plan(ctx, day, rec, base, jobs):
    trucks, night_calls = {}, {}
    for tech in ctx.crew.techs:
        route = [jobs[i] for i in rec["night"][tech]]
        calls = [call(ctx, rec, j, tech, s, e) for j, s, e in dispatch.timeline(ctx.crew, tech, route, ctx.crew.skill)]
        night_calls.update((c["job"], c) for c in calls)
        trucks[tech] = {"calls": calls, "sheet": sheet(ctx, rec, tech, route)}
    ran_tech = {i: t for _, t, i, _, _, _ in rec["visits"]}
    ran = defaultdict(list)
    for _, tech, jid, start, end, covered in rec["visits"]:
        c = call(ctx, rec, jobs[jid], tech, start, end)
        c.update(covered=covered, moved=jid in rec["moved"], unplanned=jobs[jid]["unplanned"],
                 changed=rec["changed"].get(jid))
        ran[tech].append(c)
    for tech in trucks:
        trucks[tech]["ran"] = ran[tech]
    ran_calls = {c["job"]: c for cs in ran.values() for c in cs}

    def shown(j):
        """The call as the plan holds it, or, for a call no technician took, what Claude did with it."""
        return night_calls.get(j["job"]) or ran_calls.get(j["job"]) or {
            "job": j["job"], "ai": j["ai"], "words": j["words"], "type": j["type"], "tech": None,
            "problem": ctx.labels[j["problem"]], "first_parts": [], "parts": []}

    a = [j["ai"] for j in jobs.values()]
    return {"day": day, "trucks": trucks, "todo": todo(ctx, day, rec, trucks, jobs),
            "morning": morning(ctx, rec, night_calls, ran_tech, jobs),
            "ours": tally(rec, jobs, ctx.crew), "base": tally(base, jobs, ctx.crew),
            "ai": {"coded": len(a), "sure": sum(x["first_confidence"] >= triage.BLEND_BELOW for x in a),
                   "asked": sum(bool(x["question"]) for x in a), "settled": sum(bool(x["reply"]) for x in a),
                   "calls": [shown(j) for j in jobs.values() if j["ai"]["question"]]},
            "moves": sum(r["status"] != "keep" for t in trucks.values() for r in t["sheet"]["rows"])}


def opens_on(d):
    """A day the page may open on: a sick call and a breakdown that each moved at least one call."""
    m = d["morning"]
    gave = {x["from"] for x in m["moved"]}
    return bool(gave & set(m["sick"])) and bool(gave & set(m["broken"]))


def build(conn, answers, followups):
    """The week, replayed under the Staple rule and the nearest-technician baseline, with every day shaped. Claude's
    answers and questions are handed in and never loaded here, so the plan runs on the answers the run scored."""
    raw, first = week_jobs(conn)
    jobs = recode(raw, answers, followups)
    seed = dispatch.SEED + first
    ours = dispatch.replay(conn, jobs, "skills and parts", "balanced", record=True, seed=seed)
    base = dispatch.replay(conn, jobs, "nearest", "today", record=True, seed=seed)
    ctx = Context(conn, jobs)
    by_day = defaultdict(dict)
    for j in jobs:
        by_day[j["day"]][j["job"]] = j
    days = [day_plan(ctx, day, ours["days"][day], base["days"][day], by_day[day]) for day in sorted(by_day)]
    eventful = [d["day"] for d in days if opens_on(d)]
    return {"days": days, "open": eventful[-1] if eventful else days[-1]["day"], "first": first, "jobs": jobs,
            "ours": ours, "base": base, "ctx": ctx, "zones": ZONES,
            "centers": list(conn.execute("SELECT center, zone FROM centers ORDER BY center"))}
