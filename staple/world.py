"""Builds the made-up world: three years of repairs, plus technicians, trucks, zones and centres.

Everything here is invented: no real company, brand, model or part number. A fixed seed means
every run writes the same rows, so every number downstream can be reproduced.
"""
import datetime as dt
import math
import random

SEED = 2026
START = dt.date(2023, 11, 1)
DAYS = 1096                      # 2023-11-01 to 2026-10-31
TEST_DAYS = 92                   # the last three months are held back for the back-test
JOBS = 45_000
MISDESCRIBED = 0.10              # customer describes a different problem than the real one
RANDOM_FAILURE = 0.08            # the failed part follows no pattern at all
VAGUE_WORDS = 0.08               # customer gives words that fit no single problem

# type -> (openers, parts, problems). A part is (name, cost $, size, locked).
# A problem is code -> (label, phrasings, {part name: weight}).
CATALOGUE = {
    "refrigerator": (
        ["My fridge", "The refrigerator"],
        [("defrost heater", 38, 2, 0), ("defrost thermostat", 22, 1, 0), ("evaporator fan motor", 64, 2, 0),
         ("condenser fan motor", 58, 2, 0), ("main control board", 189, 2, 0), ("start relay", 26, 1, 0),
         ("water inlet valve", 41, 1, 0), ("ice maker assembly", 112, 3, 0), ("door gasket", 69, 3, 0),
         ("sealed-system compressor", 420, 8, 1)],
        {"FF_WARM": ("Fresh-food side warm, freezer cold",
                     ["is warm in the fridge side but the freezer is fine", "won't keep the milk cold, freezer still freezes",
                      "fridge section is getting warm"],
                     {"defrost heater": 34, "defrost thermostat": 20, "evaporator fan motor": 26, "main control board": 8}),
         "NOT_COOLING": ("Not cooling at all",
                         ["is not cooling at all", "is warm everywhere, freezer too", "stopped getting cold"],
                         {"start relay": 25, "condenser fan motor": 22, "main control board": 18,
                          "sealed-system compressor": 15, "evaporator fan motor": 8}),
         "ICE_MAKER": ("Ice maker not making ice",
                       ["isn't making any ice", "ice maker stopped working", "has no ice coming out"],
                       {"ice maker assembly": 55, "water inlet valve": 35, "main control board": 6}),
         "LEAK": ("Leaking water",
                  ["is leaking water on the floor", "has a puddle under it", "drips water inside"],
                  {"water inlet valve": 40, "defrost heater": 15, "door gasket": 12, "ice maker assembly": 10}),
         "FROST": ("Frost building up",
                   ["has frost building up in the freezer", "freezer is covered in ice", "has ice on the back wall"],
                   {"defrost heater": 38, "defrost thermostat": 30, "door gasket": 18, "main control board": 6}),
         "NOISE": ("Loud noise",
                   ["is making a loud buzzing noise", "rattles and hums loudly", "makes a grinding sound"],
                   {"evaporator fan motor": 40, "condenser fan motor": 38, "start relay": 8})}),
    "washer": (
        ["My washer", "The washing machine"],
        [("drain pump", 54, 2, 0), ("door lock", 47, 1, 0), ("water inlet valve", 39, 1, 0), ("drive belt", 18, 1, 0),
         ("shock absorbers", 33, 2, 0), ("main control board", 210, 2, 0), ("drive motor", 175, 5, 0),
         ("door boot seal", 72, 3, 0), ("pressure switch", 29, 1, 0), ("tub bearing kit", 95, 3, 0)],
        {"NO_DRAIN": ("Will not drain", ["won't drain the water", "has water sitting in the drum", "stops full of water"],
                      {"drain pump": 60, "pressure switch": 14, "main control board": 8}),
         "NO_SPIN": ("Will not spin", ["won't spin", "leaves the clothes soaking wet", "doesn't go into spin"],
                     {"drive belt": 30, "door lock": 25, "drive motor": 18, "main control board": 10, "drain pump": 8}),
         "NO_FILL": ("Will not fill", ["won't fill with water", "has no water coming in", "fills really slowly"],
                     {"water inlet valve": 62, "pressure switch": 18, "main control board": 8}),
         "LEAK": ("Leaking water", ["is leaking water", "leaves a puddle on the floor", "leaks from the door"],
                  {"door boot seal": 45, "drain pump": 18, "water inlet valve": 14}),
         "NOISE_SHAKE": ("Loud or shaking", ["shakes and bangs during spin", "makes a loud roaring noise", "walks across the floor"],
                         {"shock absorbers": 38, "tub bearing kit": 34, "drive belt": 8}),
         "NO_START": ("Will not start", ["won't start", "is dead, nothing happens", "won't lock the door and start"],
                      {"door lock": 40, "main control board": 35, "pressure switch": 6})}),
    "dryer": (
        ["My dryer", "The clothes dryer"],
        [("heating element", 46, 2, 0), ("thermal fuse", 12, 1, 0), ("igniter", 31, 1, 0), ("gas valve coils", 28, 1, 1),
         ("drive belt", 16, 1, 0), ("idler pulley", 19, 1, 0), ("drum rollers", 24, 2, 0), ("door switch", 15, 1, 0),
         ("cycling thermostat", 21, 1, 0), ("main control board", 165, 2, 0)],
        {"NO_HEAT": ("No heat", ["runs but doesn't heat", "tumbles but the clothes stay cold", "won't get hot"],
                     {"heating element": 30, "thermal fuse": 22, "igniter": 20, "gas valve coils": 12, "cycling thermostat": 8}),
         "NO_START": ("Will not start", ["won't start", "is completely dead", "hums but won't turn"],
                      {"thermal fuse": 35, "door switch": 30, "main control board": 15, "drive belt": 6}),
         "NO_TUMBLE": ("Drum not turning", ["runs but the drum doesn't turn", "the drum won't spin", "motor runs, drum stays still"],
                       {"drive belt": 55, "idler pulley": 22, "drum rollers": 8}),
         "NOISE": ("Loud noise", ["squeals loudly", "makes a thumping noise", "is rumbling and squeaking"],
                   {"drum rollers": 42, "idler pulley": 35, "drive belt": 10}),
         "SLOW_DRY": ("Takes too long to dry", ["takes two cycles to dry", "takes forever to dry", "leaves clothes damp"],
                      {"cycling thermostat": 30, "heating element": 28, "gas valve coils": 14, "thermal fuse": 6})}),
    "dishwasher": (
        ["My dishwasher", "The dishwasher"],
        [("drain pump", 49, 2, 0), ("circulation pump", 138, 3, 0), ("water inlet valve", 36, 1, 0), ("door latch", 33, 1, 0),
         ("heating element", 44, 2, 0), ("main control board", 172, 2, 0), ("spray arm", 22, 2, 0),
         ("float switch", 17, 1, 0), ("door gasket", 31, 2, 0)],
        {"NO_DRAIN": ("Will not drain", ["won't drain", "has standing water in the bottom", "leaves dirty water inside"],
                      {"drain pump": 64, "main control board": 8}),
         "NOT_CLEANING": ("Not cleaning", ["isn't cleaning the dishes", "leaves food on everything", "dishes come out dirty"],
                          {"circulation pump": 38, "spray arm": 30, "water inlet valve": 12}),
         "LEAK": ("Leaking water", ["is leaking from the door", "leaks onto the floor", "leaves a puddle"],
                  {"door gasket": 44, "float switch": 16, "water inlet valve": 14, "circulation pump": 8}),
         "NO_START": ("Will not start", ["won't start", "has no lights at all", "won't run a cycle"],
                      {"door latch": 42, "main control board": 38}),
         "NO_DRY": ("Dishes not drying", ["doesn't dry the dishes", "everything comes out wet", "drying stopped working"],
                    {"heating element": 64, "main control board": 12})}),
    "range": (
        ["My oven", "The range"],
        [("bake element", 42, 2, 0), ("broil element", 44, 2, 0), ("oven igniter", 58, 1, 0), ("surface burner switch", 34, 1, 0),
         ("temperature sensor", 27, 1, 0), ("main control board", 198, 2, 0), ("door hinge", 23, 1, 0),
         ("gas safety valve", 96, 2, 1), ("spark module", 61, 1, 0)],
        {"OVEN_NO_HEAT": ("Oven not heating", ["oven won't heat up", "oven stays cold", "bake doesn't work"],
                         {"bake element": 34, "oven igniter": 34, "gas safety valve": 10, "main control board": 8}),
         "BURNER_NO_LIGHT": ("Burner will not light", ["burner won't light", "keeps clicking but no flame", "top burner is dead"],
                            {"spark module": 42, "surface burner switch": 38}),
         "TEMP_WRONG": ("Temperature off", ["burns everything, runs too hot", "oven temperature is way off", "takes forever to bake"],
                        {"temperature sensor": 60, "main control board": 22}),
         "NO_POWER": ("No power", ["is completely dead", "display is blank", "nothing comes on"],
                      {"main control board": 70, "surface burner switch": 6}),
         "DOOR": ("Door problem", ["door won't close properly", "door is hanging crooked", "door hinge broke"],
                  {"door hinge": 84})}),
    "furnace": (
        ["My furnace", "The heating system"],
        [("hot surface igniter", 52, 1, 0), ("flame sensor", 24, 1, 0), ("inducer motor", 230, 4, 0),
         ("pressure switch", 48, 1, 0), ("main control board", 245, 2, 0), ("gas valve", 185, 2, 1),
         ("blower motor", 310, 6, 0), ("run capacitor", 19, 1, 0), ("limit switch", 31, 1, 0)],
        {"NO_HEAT": ("No heat", ["isn't putting out any heat", "blows cold air", "the house won't warm up"],
                     {"hot surface igniter": 30, "flame sensor": 24, "gas valve": 12, "main control board": 12}),
         "SHORT_CYCLE": ("Short cycling", ["turns on and off every few minutes", "keeps shutting off", "starts then stops"],
                         {"flame sensor": 40, "limit switch": 28, "pressure switch": 16}),
         "BLOWER_ONLY": ("Blower problem", ["fan won't come on", "no air coming out of the vents", "blower runs nonstop"],
                         {"run capacitor": 40, "blower motor": 30, "main control board": 18}),
         "NOISE": ("Loud noise", ["is making a loud humming noise", "rattles when it starts", "squeals loudly"],
                   {"inducer motor": 46, "blower motor": 28, "run capacitor": 10}),
         "NO_START": ("Will not start", ["won't start at all", "is totally dead", "the thermostat calls but nothing happens"],
                      {"main control board": 34, "pressure switch": 30, "limit switch": 14})}),
}
VAGUE = ["is acting up and not working right", "is broken and making a strange noise", "stopped working properly"]
DETAILS = ["", " since yesterday"]
FAMILY_PREFIX = {"refrigerator": "RF", "washer": "WM", "dryer": "DR", "dishwasher": "DW", "range": "RG", "furnace": "FN"}
FAMILIES_PER_TYPE = 10
RARE_FAMILIES = 2               # the newest families, introduced recently, so their history is thin
ZONES = [(x, y) for y in range(3) for x in range(4)]   # 12 zones, 15 km apart
TECHS = 30


def part_id(appliance, name):
    return f"{appliance}/{name}"


def problem_id(appliance, code):
    return f"{appliance}.{code}"


def customer_words(rng, appliance, openers, phrasings):
    return f"{rng.choice(openers)} {rng.choice(phrasings)}{rng.choice(DETAILS)}"


def pick_parts(rng, weights, weak_part):
    """One or two parts for a job; the family's weak part fails more often where it can fail at all."""
    names = list(weights)
    w = [weights[n] * (4 if n == weak_part else 1) for n in names]
    roll = rng.random()
    if roll < 0.07:
        return []                                   # an adjustment, no part fitted
    first = rng.choices(names, w)[0]
    if roll < 0.80 or len(names) == 1:
        return [first]
    rest = [n for n in names if n != first]
    return [first, rng.choices(rest, [weights[n] for n in rest])[0]]


def build(conn, seed=SEED):
    rng = random.Random(seed)
    with open(_schema_path(), encoding="utf-8") as f:
        conn.executescript(f.read())

    for appliance, (_, parts, problems) in CATALOGUE.items():
        conn.execute("INSERT INTO appliance_types VALUES (?)", (appliance,))
        conn.executemany("INSERT INTO parts VALUES (?,?,?,?,?,?)",
                         [(part_id(appliance, n), appliance, n, c, s, lk) for n, c, s, lk in parts])
        conn.executemany("INSERT INTO problems VALUES (?,?,?)",
                         [(problem_id(appliance, code), appliance, p[0]) for code, p in problems.items()])

    families = {}
    for appliance, (_, parts, problems) in CATALOGUE.items():
        shared = [n for n, *_ in parts if sum(n in p[2] for p in problems.values()) >= 2]
        for i in range(FAMILIES_PER_TYPE):
            fam = f"{FAMILY_PREFIX[appliance]}-{(i + 1) * 10 + 200}"
            rare = i >= FAMILIES_PER_TYPE - RARE_FAMILIES
            first_day = DAYS - 200 if rare else 0
            weak = rng.choice(shared)
            popularity = (0.15 if rare else 1.0) / (i + 1) ** 0.9
            families[fam] = (appliance, weak, first_day, popularity)
            conn.execute("INSERT INTO families VALUES (?,?,?,?)",
                         (fam, appliance, part_id(appliance, weak), str(START + dt.timedelta(days=first_day))))

    conn.executemany("INSERT INTO zones VALUES (?,?,?)", [(i, x * 15.0, y * 15.0) for i, (x, y) in enumerate(ZONES)])

    techs = _build_techs(conn, rng)
    jobs, job_parts = [], []
    test_from = DAYS - TEST_DAYS
    for job in range(1, JOBS + 1):
        day = rng.randrange(DAYS)
        date = START + dt.timedelta(days=day)
        appliance = _season_pick(rng, date.month)
        openers, _, problems = CATALOGUE[appliance]
        fam = _family_pick(rng, families, appliance, day)
        true_code = rng.choice(list(problems))
        noise, booked_code = "none", true_code
        if rng.random() < MISDESCRIBED:
            noise, booked_code = "misdescribed", rng.choice([c for c in problems if c != true_code])
        weights = problems[true_code][2]
        if rng.random() < RANDOM_FAILURE:
            noise = "random"
            weights = {n: 1 for n, *_ in CATALOGUE[appliance][1]}
        needed = pick_parts(rng, weights, families[fam][1])
        phrasings = VAGUE if rng.random() < VAGUE_WORDS else problems[booked_code][1]
        words = customer_words(rng, appliance, openers, phrasings)
        zone = rng.randrange(len(ZONES))
        tech = _history_tech(rng, techs, appliance, zone)
        on_truck = all(part_id(appliance, n) in techs[tech]["stock"] for n in needed)
        fixed = on_truck and rng.random() < techs[tech]["skills"][appliance][0]
        jobs.append((job, str(date), zone, appliance, fam, problem_id(appliance, booked_code),
                     problem_id(appliance, true_code), words, noise, tech, int(on_truck), int(fixed),
                     "test" if day >= test_from else "train"))
        job_parts += [(job, part_id(appliance, n)) for n in needed]
    conn.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", jobs)
    conn.executemany("INSERT INTO job_parts VALUES (?,?)", job_parts)

    _build_centres(conn, rng)
    conn.commit()


def _schema_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def _season_pick(rng, month):
    """Furnace calls triple in winter and nearly vanish in summer; everything else is steady."""
    furnace = 3.0 if month in (11, 12, 1, 2) else 0.3 if month in (6, 7, 8) else 1.0
    types = list(CATALOGUE)
    return rng.choices(types, [furnace if t == "furnace" else 1.0 for t in types])[0]


def _family_pick(rng, families, appliance, day):
    live = [(f, v[3]) for f, v in families.items() if v[0] == appliance and v[2] <= day]
    return rng.choices([f for f, _ in live], [p for _, p in live])[0]


def _build_techs(conn, rng):
    """30 technicians who differ in level, range and skill. Each drives one truck."""
    levels = {"junior": (0.78, 1.20), "mid": (0.86, 1.00), "senior": (0.92, 0.90)}
    national_top = _national_top_parts(per_type=8)
    techs = {}
    for i in range(1, TECHS + 1):
        tech = f"T{i:02d}"
        level = rng.choices(list(levels), [25, 45, 30])[0]
        profile = "specialist" if rng.random() < 0.4 else "generalist"
        types = rng.sample(list(CATALOGUE), rng.choice([2, 3])) if profile == "specialist" else list(CATALOGUE)
        certified = int(level == "senior" or (level == "mid" and rng.random() < 0.5))
        base_rate, base_speed = levels[level]
        skills = {}
        for t in types:
            rate = min(0.98, base_rate + (0.05 if profile == "specialist" else 0) + rng.uniform(-0.03, 0.03))
            speed = base_speed * (0.8 if profile == "specialist" else 1.0) * rng.uniform(0.95, 1.05)
            skills[t] = (round(rate, 3), round(speed, 3))
        stock = {}
        depth = 8 if profile == "specialist" else 6       # specialists use their spare space to stock deeper
        for t in types:
            for name in national_top[t][:depth]:
                pid = part_id(t, name)
                if _locked(t, name) and not certified:
                    continue
                stock[pid] = 1
        home = rng.randrange(len(ZONES))
        techs[tech] = {"skills": skills, "stock": stock, "home": home}
        conn.execute("INSERT INTO techs VALUES (?,?,?,?,?)", (tech, level, profile, home, certified))
        conn.executemany("INSERT INTO tech_skills VALUES (?,?,?,?)", [(tech, t, r, s) for t, (r, s) in skills.items()])
        conn.execute("INSERT INTO trucks VALUES (?,?,?)", (f"V{i:02d}", tech, 90))
        conn.executemany("INSERT INTO truck_stock VALUES (?,?,?)", [(f"V{i:02d}", p, q) for p, q in stock.items()])
    return techs


def _national_top_parts(per_type=4):
    """Today's stocking policy: each truck carries the parts most often used for each appliance type."""
    top = {}
    for appliance, (_, _, problems) in CATALOGUE.items():
        totals = {}
        for _, _, weights in problems.values():
            share = sum(weights.values())
            for n, w in weights.items():
                totals[n] = totals.get(n, 0) + w / share
        top[appliance] = sorted(totals, key=lambda n: totals[n], reverse=True)[:per_type]
    return top


def _locked(appliance, name):
    return any(n == name and lk for n, _, _, lk in CATALOGUE[appliance][1])


def _history_tech(rng, techs, appliance, zone):
    """The technician history says went: one who does this appliance, more likely the nearer they live."""
    able = [t for t, v in techs.items() if appliance in v["skills"]]
    weights = [1.0 / (1.0 + travel_minutes(techs[t]["home"], zone)) for t in able]
    return rng.choices(able, weights)[0]


def travel_minutes(zone_a, zone_b):
    """Zone-to-zone drive time: 12 minutes inside a zone, then 1.2 minutes per km between centres."""
    if zone_a == zone_b:
        return 12.0
    (xa, ya), (xb, yb) = ZONES[zone_a], ZONES[zone_b]
    return 10.0 + 1.2 * 15.0 * math.hypot(xa - xb, ya - yb)


def _build_centres(conn, rng):
    """Two distribution centres restock trucks between workdays; a few parts are on back-order today."""
    conn.executemany("INSERT INTO centres VALUES (?,?)", [("North centre", 1), ("South centre", 10)])
    rows = []
    for appliance, (_, parts, _) in CATALOGUE.items():
        for n, *_ in parts:
            if rng.random() < 0.08:
                rows.append((part_id(appliance, n), "backorder", rng.randint(3, 14)))
            else:
                rows.append((part_id(appliance, n), "in_stock", None))
    conn.executemany("INSERT INTO centre_stock VALUES (?,?,?)", rows)
