"""Worked examples for the methods page, one per section that computes a figure.

Each example is data: plain words, small made-up inputs, the steps in words, the answer worked by hand, and a
compute that calls the engine's own function on those inputs. The page prints the computed answer, and
`check.py examples` refuses an example whose hand answer disagrees with it. The inputs are tiny and independent of
the seeded world, so only a change to the engine's arithmetic can move an answer.
"""
from dataclasses import dataclass

from staple import backorders, backtest, db, dispatch, load, metrics, plan, triage
from staple.predict import PULL, Predictor


@dataclass(frozen=True)
class Example:
    section: str      # the methods-page section it sits in; its anchor is ex-<section>
    title: str
    words: str
    inputs: tuple     # (column headings, rows)
    steps: tuple
    by_hand: object
    compute: object   # calls the engine's own function on the inputs
    show: object      # how the computed answer reads on the page
    source: str
    lead: str = ""
    formula: str = ""

    @property
    def id(self):
        return f"ex-{self.section}"


FIVE = ((1, "2026-03-04", "WA-1", "NO_DRAIN", "drain pump", 1),   # job, day, family, booked as, part needed, fixed
        (2, "2026-03-18", "WA-1", "NO_DRAIN", "drain pump", 0),
        (3, "2026-03-25", "WA-2", "NO_DRAIN", "door lock", 1),
        (4, "2026-04-02", "WA-1", "NO_SPIN", "drive belt", 1),
        (5, "2026-04-20", "WA-2", "NO_SPIN", "drain pump", 1))
LABEL = {"NO_DRAIN": "Will not drain", "NO_SPIN": "Will not spin"}
FIVE_TABLE = (("Job", "Day", "Model family", "Booked as", "Part needed", "Fixed on the first visit"),
              tuple((j, day, fam, LABEL[q], part, "yes" if fix else "no") for j, day, fam, q, part, fix in FIVE))


def five_jobs():
    """The five washer jobs above as a database of their own, in memory, on Staple's real tables."""
    conn = db.connect(":memory:")
    conn.executescript((metrics.SQL.parent / "schema.sql").read_text(encoding="utf-8"))
    conn.executemany("INSERT INTO parts VALUES (?, 'washer', ?, 20.0, 1, 0)",
                     [(f"washer/{p}", p) for p in sorted({row[4] for row in FIVE})])
    conn.executemany("INSERT INTO jobs VALUES (?, ?, 0, 'washer', ?, ?, ?, '', 'none', 'T01', 1, ?, 'train')",
                     [(j, day, fam, f"washer.{q}", f"washer.{q}", fix) for j, day, fam, q, _, fix in FIVE])
    conn.executemany("INSERT INTO job_parts VALUES (?, ?)", [(j, f"washer/{p}") for j, _, _, _, p, _ in FIVE])
    return conn


HELD_BACK = ({"job": 1, "needed": {"drain pump"}}, {"job": 2, "needed": {"door lock", "drive belt"}},
             {"job": 3, "needed": set()}, {"job": 4, "needed": {"shock absorbers"}},
             {"job": 5, "needed": {"drain pump", "door lock"}})
CARRY = {1: ("drain pump", "door lock", "drive belt"), 2: ("door lock", "drain pump", "water inlet valve"),
         3: ("drain pump", "door lock", "drive belt"), 4: ("drain pump", "drive belt", "door lock"),
         5: ("door lock", "drain pump", "drive belt")}
TOTALS = ((1, 150.00), (2, 131.50), (3, 130.20), (4, 129.80))
RANKED = (("drain pump", 0.62), ("drive belt", 0.20), ("door lock", 0.07), ("shock absorbers", 0.04))
MARKED = {"drain pump": "aboard", "drive belt": "from a center overnight", "door lock": "back-ordered",
          "shock absorbers": "back-ordered"}
ANSWER = {"code": "NO_HEAT", "confidence": 0.55}
FOLLOWUP = {"question": "Does the furnace not come on at all, or does it run and blow cold air?",
            "answers": [{"text": "It does not come on at all", "code": "NO_START"},
                        {"text": "It runs but blows cold air", "code": "NO_HEAT"}]}
CALLER = {"job": 1, "type": "furnace", "problem": "furnace.NO_START", "words": "the furnace is dead and the house is cold"}
CALL_LIST = (("drain pump", 0.55), ("door lock", 0.25), ("drive belt", 0.12), ("shock absorbers", 0.08))
TRUCK_NOW = {"drain pump", "shock absorbers"}
TONIGHT = {"drain pump", "drive belt", "shock absorbers"}

EXAMPLES = (
    Example(
        "predictor", "a washer's drain pump",
        "How likely a washer of family WA-1, booked as will not drain, is to need its drain pump, when these five "
        "past washer jobs are all the history there is.",
        FIVE_TABLE,
        ("Across all five washer jobs, 3 needed the drain pump: 3 / 5 = 0.6.",
         "Three were booked as will not drain, and 2 of those needed it. The pull adds 20 pretend jobs at 0.6: "
         "(2 + 20 × 0.6) / (3 + 20) = 14 / 23 = 0.6087.",
         "Two of those three were family WA-1, and both needed it. The pull adds 20 pretend jobs at 0.6087: "
         "(2 + 20 × 0.6087) / (2 + 20) = 0.6443. With so little history, the wider levels still decide most of it."),
        0.6443, lambda: Predictor(five_jobs()).odds("washer", "WA-1", "washer.NO_DRAIN")["washer/drain pump"],
        lambda v: f"{v:.4f}", "Predictor.odds",
        f"Let n be the number of training jobs at a level and c(p) how many of them needed p. With pull P = {PULL}:",
        "p_type(p) = c_a(p) / n_a\np_problem(p) = (c_q(p) + P · p_type(p)) / (n_q + P)\n"
        "p_family(p) = (c_f,q(p) + P · p_problem(p)) / (n_f,q + P)"),
    Example(
        "backtest", "five held-back jobs",
        "What share of five held-back jobs a 3-part carry list covers, where a job counts only when every part it "
        "needed was on its list.",
        (("Held-back job", "Parts it needed", "Its 3-part carry list"),
         tuple((j["job"], ", ".join(sorted(j["needed"])) or "none", ", ".join(CARRY[j["job"]])) for j in HELD_BACK)),
        ("Job 1 needed the drain pump, which is on its list: covered.",
         "Job 2 needed the door lock and the drive belt, and its list holds only the door lock: not covered, "
         "because one missing part fails the whole job.",
         "Job 3 needed no part, so any list covers it.",
         "Job 4 needed the shock absorbers, which its list lacks: not covered.",
         "Job 5 needed the drain pump and the door lock, both on its list: covered.",
         "Three of the five are covered: 3 / 5 = 0.60."),
        0.6, lambda: backtest.coverage(HELD_BACK, lambda j, k: CARRY[j["job"]][:k], 3),
        lambda v: f"{v:.2f}", "backtest.coverage",
        "For each held-back job j with needed set N(j) and carry list L_k(j):",
        f"coverage(k) = |{{ j : N(j) ⊆ L_k(j) }}| / |J_test|,   k = {min(backtest.KS)} to {max(backtest.KS)}"),
    Example(
        "loads", "choosing a list length",
        "Which list length the top-up policy is given, when four lengths cost what the table shows a truck-day.",
        (("Parts per booked call", "Cost a truck-day"), tuple((k, f"${t:.2f}") for k, t in TOTALS)),
        ("The cheapest length is 4, at $129.80 a truck-day.",
         "The tolerance is $2, so any length costing $131.80 or less is cheap enough.",
         "Length 1 costs $150.00, too much. Length 2 costs $131.50, within the tolerance, and no shorter length is.",
         "So the top-up carries 2 parts for each booked call, at $1.70 a truck-day more than the cheapest."),
        2, lambda: load.lean([{"policy": load.TOP_UP, "k": k, "total": t} for k, t in TOTALS], load.TOP_UP)["k"],
        lambda v: f"{v} parts per booked call", "load.lean",
        f"For each list policy, with total(k) its cost a truck-day at list length k and a tolerance of "
        f"${load.LEAN_TOLERANCE:.2f}:",
        f"chosen k = the shortest k with total(k) ≤ min over k' of total(k') + ${load.LEAN_TOLERANCE:.2f}"),
    Example(
        "availability", "flagging one booking",
        "Whether one booking is flagged as likely to need a part that no center can ship in time and no truck nearby "
        "holds.",
        (("Part on the booking's ranked list", "Odds it is needed", "Its mark"),
         tuple((part, f"{odds:.2f}", MARKED[part]) for part, odds in RANKED)),
        ("Only the door lock and the shock absorbers are marked back-ordered.",
         "Their odds add: 0.07 + 0.04 = 0.11.",
         "0.11 reaches the 10% flag, so the booking is flagged, though neither part alone would be."),
        (0.11, True), lambda: load.backorder_flag(RANKED, MARKED.get),
        lambda v: f"{v[0]:.2f}, {'flagged' if v[1] else 'not flagged'}", "load.backorder_flag",
        "Over booking j's ranked list, with o_j(p) its odds of needing part p:",
        f"stuck(j) = Σ_(p : mark = back-ordered) o_j(p),   flag when stuck(j) ≥ {load.BACKORDER_FLAG:.0%}"),
    Example(
        "backorders", "one back-ordered part",
        "How many calls will need one back-ordered part before it ships, how many of them no truck nearby can cover, "
        "and the repeat-visit dollars that puts at risk.",
        (("Input", "Value"),
         (("Training calls that needed the part", "240"), ("Training calls for its appliance", "4,000"),
          ("That appliance's calls a day in this month", "12.5"), ("Days until it ships", "10"),
          ("Share of its calls in zones with no holder nearby", "0.3"), ("A repeat visit", "$165"))),
        ("The part's share of its appliance's calls: 240 / 4,000 = 0.06.",
         "Calls expected before it ships: 0.06 × 12.5 calls a day × 10 days = 7.5.",
         "Of those, the share with no truck nearby holding one: 7.5 × 0.3 = 2.25 calls stuck.",
         "Each stuck call costs a repeat visit: 2.25 × $165 = $371.25 at risk."),
        (7.5, 2.25, 371.25), lambda: backorders.exposure(240, 4000, 12.5, 10, 0.3),
        lambda v: f"{v[0]:.1f} calls expected, {v[1]:.2f} stuck, ${v[2]:,.2f} at risk", "backorders.exposure",
        "From training only, for part p of appliance a, with its back-order starting in month m:",
        "calls_a(m) = training calls of appliance a in month m / training days in month m\n"
        "share(p)   = training calls needing p / training calls of a\n"
        "expected(p) = share(p) · calls_a(m) · ETA_p\n"
        f"open zones  = zones with no holding truck based within {load.NEARBY_MINUTES} minutes\n"
        "stuck_share(p) = Σ_(z ∈ open) (calls_a,z − Σ_(holder t) calls_a,z,t) / calls_a\n"
        "stuck(p)   = expected(p) · stuck_share(p),   at_risk(p) = stuck(p) · R"),
    Example(
        "ai", "one unsure booking",
        "Which problem code a booking runs on when Claude is unsure of the customer's words and asks its one saved "
        "question.",
        (("Input", "Value"),
         (("The customer's words", CALLER["words"]), ("Claude's first reading", "NO_HEAT, 0.55 sure"),
          ("Its saved question", FOLLOWUP["question"]),
          ("The offered answers", "; ".join(f"{a['text']}: {a['code']}" for a in FOLLOWUP["answers"])),
          ("The problem the customer booked", "NO_START"))),
        ("Claude is 0.55 sure, below the 0.7 it needs, and a saved question exists, so it asks.",
         "The simulated customer picks the offered answer that matches what they booked: It does not come on at "
         "all, which points to NO_START.",
         "An answer that fits settles the code, so the call runs on NO_START at full confidence, 1.0."),
        ("NO_START", 1.0), lambda: triage.asked(ANSWER, FOLLOWUP, CALLER, triage.honest),
        lambda v: f"{v[0]}, at confidence {v[1]:.1f}", "triage.asked"),
    Example(
        "dispatch", "one truck's parts risk",
        "The chance a call needs a part this technician's truck does not hold right now, which the dispatch rule "
        "turns into the cover in its cost.",
        (("Part on the call's list", "Odds it is needed", "On this truck now"),
         tuple((part, f"{odds:.2f}", "yes" if part in TRUCK_NOW else "no") for part, odds in CALL_LIST)),
        ("The drain pump and the shock absorbers are aboard, so they add no risk.",
         "The door lock and the drive belt are not: 0.25 + 0.12 = 0.37.",
         "That is below 1, so the cap leaves it alone, and the call's cover is 1 − 0.37 = 0.63."),
        0.37, lambda: dispatch.miss_odds({7: CALL_LIST}, {"job": 7}, lambda p: p in TRUCK_NOW),
        lambda v: f"{v:.2f}", "dispatch.miss_odds",
        "With drive minutes from the end of t's route so far, and believed rate r̂ and time multiplier v̂ for t on "
        "j's appliance:",
        f"minutes = drive + repair_a · v̂\nnearest:          cost = drive\n"
        f"skills only:      cost = minutes · ${dispatch.MINUTE:.0f} + (1 − r̂) · R\n"
        f"skills and parts: cost = minutes · ${dispatch.MINUTE:.0f} + (1 − cover · r̂) · R\n"
        "cover = 1 − min(1, Σ_(p not available to t) o_j(p))"),
    Example(
        "plan", "one call's first-time fix chance",
        "The plan's chance that one washer call is fixed on the first visit by the truck loaded for it tonight.",
        (("Input", "Value"),
         (("Parts loaded tonight, with the call's odds of needing each",
           ", ".join(f"{p} {o:.2f}" for p, o in CALL_LIST if p in TONIGHT)),
          ("Part not loaded", ", ".join(f"{p} {o:.2f}" for p, o in CALL_LIST if p not in TONIGHT)),
          ("The technician's success rate on washers", "0.88"))),
        ("The only part the truck lacks is the door lock, at 0.25, so the chance the truck holds everything the call "
         "needs is 1 − 0.25 = 0.75.",
         "The technician fixes 0.88 of washers when every part is there.",
         "Both must hold: 0.75 × 0.88 = 0.66."),
        0.66, lambda: plan.first_visit_chance({9: CALL_LIST}, {"job": 9}, TONIGHT, 0.88),
        lambda v: f"{v:.2f}", "plan.first_visit_chance",
        "For call j on technician t's truck, with o_j(p) the carry list's odds and r_t,a the technician's rate on the "
        "appliance:",
        "chance(j, t) = (1 − min(1, Σ_(p not aboard t) o_j(p))) · r_t,a"),
    Example(
        "sql", "first-time fix by month",
        "What the first-time fix query returns when the five washer jobs from the predictor's example are the whole "
        "history.",
        FIVE_TABLE,
        ("The query groups the jobs by the month of their day.",
         "March holds jobs 1, 2 and 3, and jobs 1 and 3 were fixed on the first visit: 2 / 3 = 66.7%.",
         "April holds jobs 4 and 5, both fixed on the first visit: 100.0%."),
        [("2026-03", 3, 66.7), ("2026-04", 2, 100.0)],
        lambda: five_jobs().execute((metrics.SQL / "first_time_fix.sql").read_text(encoding="utf-8")).fetchall(),
        lambda v: "; ".join(f"{m}: {n} jobs, {p}%" for m, n, p in v), "sql/metrics/first_time_fix.sql"),
)


def agrees(computed, by_hand, tolerance=5e-5):
    """Whether the engine's answer matches the hand answer, number by number within the hand answer's rounding."""
    if isinstance(by_hand, (bool, str)) or isinstance(computed, (bool, str)):
        return computed == by_hand
    if isinstance(by_hand, (int, float)):
        return isinstance(computed, (int, float)) and abs(computed - by_hand) <= tolerance
    return len(computed) == len(by_hand) and all(agrees(c, h, tolerance) for c, h in zip(computed, by_hand))
