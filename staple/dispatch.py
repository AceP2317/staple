"""Dispatch replay: who takes each call, on which truck, and what a day's surprises do to the plan.

Every held-back day is replayed under each policy with the same made-up events. The night before, booked
calls are assigned in priority order and each truck is loaded for its route. Within one priority, the call
with most to lose if its best technician fills up is placed first. Then, before 08:00, some
technicians call out sick; during the day unplanned calls arrive, a truck may break down and strand its
stock, and some calls escalate or ease. Calls moved during the day can use only what the other trucks
already carry. At day's end a route that runs past 17:00 drops its lowest-priority calls to tomorrow.

Staple's rule sends each call to the technician whose expected cost is lowest: drive and repair minutes,
plus a second visit times the chance this technician fails, from their success rate on the appliance and
whether their truck can carry the parts. The baseline sends the nearest qualified technician.
"""
import random
from collections import Counter, defaultdict

from staple import load
from staple.backtest import test_jobs
from staple.predict import Predictor
from staple.world import travel_minutes

DAY = 540                # minutes from 08:00 to 17:00
REPAIR = {"refrigerator": 75, "washer": 60, "dryer": 50, "dishwasher": 55, "range": 50, "furnace": 70}  # made up
MINUTE = 1.0             # dollars for one minute of a technician and truck (made up)
SICK, BREAKDOWN, ESCALATE, EASE = 0.03, 0.02, 0.05, 0.05   # daily chances, made up
URGENT = {"refrigerator.NOT_COOLING", "refrigerator.FF_WARM", "furnace.NO_HEAT", "furnace.NO_START"}
LOW = {"refrigerator.NOISE", "dryer.NOISE", "furnace.NOISE", "range.DOOR", "washer.NOISE_SHAKE"}


class Crew:
    """Each technician's success rate and speed per appliance, and what the dispatcher believes them to be."""

    def __init__(self, conn, shuffled=False, seed=0, blind=False):
        rows = conn.execute("SELECT tech, type, success_rate, speed FROM tech_skills ORDER BY tech, type").fetchall()
        self.skill = {(t, a): (rate, speed) for t, a, rate, speed in rows}
        self.home = dict(conn.execute("SELECT tech, home_zone FROM techs"))
        self.techs = sorted(self.home)
        self.belief = dict(self.skill)
        if shuffled:
            # The control: the dispatcher reads another technician's numbers; who can do which appliance stays true.
            keys = sorted(self.skill)
            values = [self.skill[k] for k in keys]
            random.Random(seed).shuffle(values)
            self.belief = dict(zip(keys, values))
        if blind:
            # A dispatcher that knows who can do which appliance but takes every technician as average.
            rate = sum(r for r, _ in self.skill.values()) / len(self.skill)
            self.belief = {k: (rate, 1.0) for k in self.skill}


def priority(job, changed):
    return changed.get(job["job"]) or (1 if job["problem"] in URGENT else 3 if job["problem"] in LOW else 2)


def events(day_jobs, techs, seed):
    """The day's surprises, drawn once and shared by every policy."""
    rng = random.Random(seed)
    sick = {t for t in techs if rng.random() < SICK}
    broken = {t: rng.uniform(120, 360) for t in techs if rng.random() < BREAKDOWN}
    changed = {}
    for j in day_jobs:
        roll = rng.random()
        if roll < ESCALATE:
            changed[j["job"]] = 1
        elif roll < ESCALATE + EASE:
            changed[j["job"]] = 3
    return sick, broken, changed


def timeline(crew, tech, route, skills):
    """Each call on a route with its start and end, in minutes after 08:00."""
    zone, clock, out = crew.home[tech], 0.0, []
    for j in route:
        start = clock + travel_minutes(zone, j["zone"])
        clock = start + REPAIR[j["type"]] * skills[(tech, j["type"])][1]
        out.append((j, start, clock))
        zone = j["zone"]
    return out


def end_of(crew, tech, route, skills):
    line = timeline(crew, tech, route, skills)
    return (line[-1][2], line[-1][0]["zone"]) if line else (0.0, crew.home[tech])


def miss_odds(lists, job, has):
    """The chance a call needs a part the truck lacks, from the predictor's odds for that call."""
    return min(1.0, sum(p for part, p in lists[job["job"]] if not has(part)))


def replay(conn, jobs, rule, loads="balanced", crew=None, record=False, seed=2026, part_odds=None):
    """One policy over every held-back day. rule is "nearest", "skills" or "skills and parts". part_odds, when
    given, replaces the predictor's odds in the dispatcher's parts judgment only; trucks still load on the real ones."""
    crew = crew or Crew(conn)
    fleet, predictor = load.Fleet(conn), Predictor(conn)
    lists, surprise = load.ranked_lists(predictor, jobs), load.surprise_value(conn, predictor)
    judged = part_odds or lists
    by_day = defaultdict(list)
    for j in jobs:
        by_day[j["day"]].append(j)
    before = {t: set(fleet.stock[t]) for t in crew.techs}
    t = Counter()
    kept, rolled_ids, happened = [], [], {}
    for d, day in enumerate(sorted(by_day)):
        day_jobs = by_day[day]
        sick, broken, changed = events(day_jobs, crew.techs, seed + d)
        routes, out, truck = {tech: [] for tech in crew.techs}, set(), {}
        ends = {tech: (0.0, crew.home[tech]) for tech in crew.techs}
        rolled = []

        def able(job):
            return [tech for tech in crew.techs if (tech, job["type"]) in crew.skill and tech not in out]

        def cost_of(job, tech, night):
            # The calendar runs on real durations; only the choice between technicians uses the beliefs.
            clock, zone = ends[tech]
            drive = travel_minutes(zone, job["zone"])
            if clock + drive + REPAIR[job["type"]] * crew.skill[(tech, job["type"])][1] > DAY:
                return None
            if rule == "nearest":
                return drive
            minutes = drive + REPAIR[job["type"]] * crew.belief[(tech, job["type"])][1]
            has = (lambda p: fleet.may_carry(tech, p)) if night else (lambda p: p in truck[tech])
            cover = 1.0 if rule == "skills" else 1.0 - miss_odds(judged, job, has)
            return minutes * MINUTE + (1 - cover * crew.belief[(tech, job["type"])][0]) * load.REPEAT_VISIT

        def put(job, tech):
            routes[tech].append(job)
            ends[tech] = end_of(crew, tech, routes[tech], crew.skill)

        def place(job, night):
            options = [(c, tech) for tech in able(job) if (c := cost_of(job, tech, night)) is not None]
            if options:
                put(job, min(options)[1])
            else:
                rolled.append(job)

        def plan_night(band):
            # Regret insertion: the call that loses most if its best technician fills up is placed first, so
            # a scarce technician's day goes to the calls that need them most, not to the first ones booked.
            # ponytail: a heuristic, not the optimum; with unlimited days Staple reaches 91.0% first-visit fix,
            # which bounds what a solver (min-cost assignment per day) could still win back.
            costs = {j["job"]: {tech: cost_of(j, tech, True) for tech in able(j)} for j in band}
            waiting = {j["job"]: j for j in band}

            def rank(jid):
                c = sorted((v, t) for t, v in costs[jid].items() if v is not None)
                return (c[1][0] - c[0][0] if len(c) > 1 else float("inf"), c[0][1]) if c else None

            ranks = {jid: rank(jid) for jid in waiting}
            while waiting:
                stuck = [jid for jid, r in ranks.items() if r is None]
                for jid in stuck:
                    rolled.append(waiting.pop(jid))
                    del ranks[jid]
                if not waiting:
                    break
                jid = max(ranks, key=lambda i: (ranks[i][0], -i))
                tech = ranks.pop(jid)[1]
                put(waiting.pop(jid), tech)
                for other, j in waiting.items():
                    if tech in costs[other]:
                        costs[other][tech] = cost_of(j, tech, True)
                        ranks[other] = rank(other)

        night = sorted((j for j in day_jobs if not j["unplanned"]), key=lambda j: (priority(j, {}), j["job"]))
        for band in (1, 2, 3):
            plan_night([j for j in night if priority(j, {}) == band])
        if loads == "balanced":
            truck = {tech: load.balanced_load(fleet, tech, routes[tech], lists, surprise, before[tech])
                     for tech in crew.techs}
            before = truck
        else:
            truck = {tech: set(fleet.stock[tech]) for tech in crew.techs}
        moved = Counter()
        out |= sick                      # sick calls come in at 07:00, after the routes are planned and loaded
        for tech in sorted(sick):
            stranded, routes[tech] = routes[tech], []
            for j in stranded:
                moved[j["job"]] += 1
                place(j, night=False)
        for j in sorted((j for j in day_jobs if j["unplanned"]), key=lambda j: (priority(j, changed), j["job"])):
            place(j, night=False)
        for tech, at in sorted(broken.items()):
            if tech in out:
                continue
            out.add(tech)
            line = timeline(crew, tech, routes[tech], crew.skill)
            routes[tech] = [j for j, start, _ in line if start < at]
            for j, start, _ in line:
                if start >= at:
                    moved[j["job"]] += 1
                    place(j, night=False)
        for tech in crew.techs:
            # ponytail: the day's end drops the lowest-priority call, last first, until the route fits; a real
            # scheduler would re-sequence the route instead. Drive time is zone to zone, not road routing.
            while routes[tech] and end_of(crew, tech, routes[tech], crew.skill)[0] > DAY:
                drop = max(range(len(routes[tech])), key=lambda i: (priority(routes[tech][i], changed), i))
                rolled.append(routes[tech].pop(drop))
        for tech, route in routes.items():
            previous = 0.0
            for j, start, end in timeline(crew, tech, route, crew.skill):
                covered = j["needed"] <= truck[tech]
                fix = covered * crew.skill[(tech, j["type"])][0]
                t["served"] += 1
                t["fixes"] += fix
                t["parts_missing"] += not covered
                t["drive"] += start - previous
                t["repair"] += end - start
                t["moved"] += bool(moved[j["job"]])
                t["moved_fixes"] += fix if moved[j["job"]] else 0
                previous = end
                if record:
                    kept.append((day, tech, j["job"], start, end, covered))
        if record:
            rolled_ids.extend(j["job"] for j in rolled)
            happened[day] = (sick, broken)
        t["rolled"] += len(rolled)
        t["urgent_rolled"] += sum(1 for j in rolled if priority(j, changed) == 1)
        t["sick"] += len(sick)
        t["broken"] += len([x for x in broken if x not in sick])
    calls = len(jobs)
    result = {"calls": calls, "served": t["served"], "rolled": t["rolled"], "urgent_rolled": t["urgent_rolled"],
              "fix_rate": t["fixes"] / t["served"], "fixes_per_call": t["fixes"] / calls,
              "parts_missing": t["parts_missing"] / t["served"], "drive_per_call": t["drive"] / t["served"],
              "moved": t["moved"], "moved_fix_rate": t["moved_fixes"] / t["moved"] if t["moved"] else 0.0,
              "sick": t["sick"], "broken": t["broken"],
              "cost_per_call": (t["drive"] + t["repair"]) * MINUTE / t["served"]
                               + (1 - t["fixes"] / t["served"]) * load.REPEAT_VISIT}
    if record:
        result.update(visits=kept, rolled_jobs=rolled_ids, events=happened)
    return result


POLICIES = [("nearest technician, today's stock", "nearest", "today", {}),
            ("nearest technician, balanced loads", "nearest", "balanced", {}),
            ("skills only, balanced loads", "skills", "balanced", {}),
            ("parts only, balanced loads", "skills and parts", "balanced", {"blind": True}),
            ("Staple: skills and parts, balanced loads", "skills and parts", "balanced", {})]
SHUFFLED_SKILLS = "Staple with shuffled skills (control)"
SHUFFLED_ODDS = "Staple with shuffled-history parts odds (control)"


def run(conn, control_seed=5):
    """Every dispatch policy over the whole held-back period, written to dispatch. It takes no job list, so no
    caller can leave the table holding a subset; replay() runs one policy on any jobs and writes nothing."""
    jobs = test_jobs(conn)
    load.mark_unplanned(jobs)
    out = {name: replay(conn, jobs, rule, loads, Crew(conn, **crew)) for name, rule, loads, crew in POLICIES}
    out[SHUFFLED_SKILLS] = replay(conn, jobs, "skills and parts", "balanced", Crew(conn, shuffled=True, seed=control_seed))
    out[SHUFFLED_ODDS] = replay(conn, jobs, "skills and parts", "balanced",
                                part_odds=load.ranked_lists(Predictor(conn, shuffled=True, seed=7), jobs))
    conn.execute("DELETE FROM dispatch")
    conn.executemany("INSERT INTO dispatch VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     [(name, r["calls"], r["served"], r["rolled"], r["fix_rate"], r["fixes_per_call"],
                       r["parts_missing"], r["drive_per_call"], r["moved"], r["moved_fix_rate"], r["cost_per_call"])
                      for name, r in out.items()])
    conn.commit()
    return out
