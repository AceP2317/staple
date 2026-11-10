"""Every check for Staple. `python check.py` runs them all; `python check.py <name>` runs one.

Each check prints what it measured and exits non-zero on failure. The controls exist to prove a
check can still fail: a check that never fails looks exactly like one that sees nothing.
"""
import ast
import atexit
from collections import Counter, defaultdict
import datetime
from html.parser import HTMLParser
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tokenize
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CODE_GLOBS = ["staple/*.py", "staple/pages/*.js", "sql/*.sql", "sql/metrics/*.sql", "run.py"]
NAMES_FILE = ROOT / ".private" / "banned-names.txt"
REPO_URL = "https://github.com/AceP2317/staple.git"
HOUSE_MAP = Path.home() / "dev" / "login-starter" / "scripts" / "languages.mjs"

CHECKS = {}
BY_NAME = set()     # checks that run the whole suite in another folder, so they run only when named
SKIPPED = []        # (check, the part it could not run here and why), filled as the checks run
RUNNING = [None]    # the check running now, so a skip is filed under its name


class CheckFailed(Exception):
    pass


def check(name, by_name=False):
    def register(fn):
        CHECKS[name] = fn
        if by_name:
            BY_NAME.add(name)
        return fn
    return register


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def skip(what):
    """A part of a check that cannot run in this copy. It is printed and counted, never passed in silence."""
    SKIPPED.append((RUNNING[0], what))
    print(f"  SKIPPED: {what}")


# ---- the pieces the guards are built from ---------------------------------------------------

def public_files():
    """Every file git would publish: the files git holds for the next commit."""
    from staple.report import repo_files
    return [ROOT / p for p in repo_files(ROOT)]


def code_files():
    return sorted(p for g in CODE_GLOBS for p in ROOT.glob(g))


def third_party_imports(source):
    """Top-level modules a Python source imports that neither Python nor this repo provides."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return {m for m in found if m not in sys.stdlib_module_names and m != "staple"}


def load_names():
    lines = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    return [n.strip().lower() for n in lines if n.strip() and not n.startswith("#")]


def find_names(text, names):
    return sorted({n for n in names if re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE)})


def scan_for_names(paths, names):
    hits = []
    for p in paths:
        found = find_names(p.name, names)
        try:
            found += find_names(p.read_text(encoding="utf-8"), names)
        except UnicodeDecodeError:
            pass
        if found:
            hits.append(f"{p.name}: {', '.join(sorted(set(found)))}")
    return hits


# ---- guards: what must hold before anything reaches the public repo ---------------------------

@check("guards")
def guards():
    files = code_files()
    outside = {}
    for p in files + [ROOT / "check.py"]:
        if p.suffix == ".py":
            mods = third_party_imports(p.read_text(encoding="utf-8"))
            if mods:
                outside[p.name] = sorted(mods)
    print(f"  packages beyond Python's own: {outside or 'none'}")
    require(not outside, f"third-party imports found: {outside}")

    if NAMES_FILE.exists():
        names, pub = load_names(), public_files()
        hits = scan_for_names(pub, names)
        print(f"  names scan: {len(names)} names over {len(pub)} public files, {len(hits)} hits")
        require(not hits, "names found in public files: " + "; ".join(hits))
    else:
        skip("the private-names scan. Its list is kept out of the public repository, so only the author's copy "
             "holds one, and the author's commit gate refuses to run without it")

    require(third_party_imports("import numpy\nfrom pandas import x\n") == {"numpy", "pandas"},
            "control: the import check missed numpy and pandas")
    require(not third_party_imports("import sqlite3, json\nfrom pathlib import Path\n"),
            "control: the import check flagged Python's own modules")
    print("  control: the import check fires on planted input and passes Python's own modules")

    if not hasattr(tokenize, "FSTRING_START"):
        skip(f"the scan for f-strings only Python 3.12 or newer reads. This is Python {sys.version_info[0]}."
             f"{sys.version_info[1]}, which would already have refused to load such a line")
    else:
        newer = {p.name: found for p in files + [ROOT / "check.py"] if p.suffix == ".py"
                 and (found := newer_fstrings(p.read_text(encoding="utf-8")))}
        print(f"  f-strings only Python 3.12 or newer reads: {newer or 'none'}")
        require(not newer, f"lines an older Python cannot read: {newer}")
        own, slash, fine = 'f"{d["k"]}"\n', "f'{chr(10)}{\"\\n\"}'\n", "f'{d[\"k\"]} {x!r:>{w}}'\n"
        require(newer_fstrings(own) and newer_fstrings(slash) and not newer_fstrings(fine),
                "control: the f-string scan should refuse a string in the f-string's own quotes and a backslash "
                "in a field, and pass the older form")
        print("  control: the f-string scan refuses its own quotes and a backslash in a field, and passes the older form")


def quote_of(start):
    body = start.lstrip("rRbBfFuU")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def newer_fstrings(source):
    """Lines whose f-strings need Python 3.12 or newer: inside a replacement field, a string in the f-string's
    own quotes, or a backslash. Needs a Python whose tokenizer splits f-strings, which is 3.12 or newer."""
    lines, open_quotes = set(), []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.FSTRING_START:
            if open_quotes and open_quotes[-1] in quote_of(tok.string) * 3:
                lines.add(tok.start[0])
            open_quotes.append(quote_of(tok.string))
        elif tok.type == tokenize.FSTRING_END:
            open_quotes.pop()
        elif open_quotes and tok.type != tokenize.FSTRING_MIDDLE:
            same = tok.type == tokenize.STRING and open_quotes[-1] in quote_of(tok.string) * 3 and (
                len(open_quotes[-1]) == 1 or quote_of(tok.string) == open_quotes[-1])
            if same or "\\" in tok.string:
                lines.add(tok.start[0])
    return sorted(lines)


def overweight(folder):
    """The published pages that weigh more than the limit, with their weights in bytes."""
    from staple import report
    weights = {name: (Path(folder) / name).stat().st_size for name in report.FILES.values() if (Path(folder) / name).exists()}
    return weights, {n: w for n, w in weights.items() if w > report.WEIGHT_LIMIT}


@check("weight")
def weight():
    from staple import report
    weights, heavy = overweight(report.PUBLISHED)
    print("  published pages: " + ", ".join(f"{n} {w:,} bytes" for n, w in weights.items())
          + f"; limit {report.WEIGHT_LIMIT:,} bytes each")
    require(len(weights) == len(report.FILES), f"docs/ is missing pages: {sorted(set(report.FILES.values()) - set(weights))}")
    require(not heavy, f"pages over the weight limit: {heavy}")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for name in report.FILES.values():
            page = (report.PUBLISHED / name).read_text(encoding="utf-8")
            if name == "index.html":
                page += "<!--" + "x" * (report.WEIGHT_LIMIT - len(page.encode("utf-8")) + 1) + "-->"
            (Path(tmp) / name).write_text(page, encoding="utf-8")
        _, padded = overweight(tmp)
    require(list(padded) == ["index.html"], f"control: a page padded past the limit was not refused: {padded}")
    print(f"  control: index.html padded to {padded['index.html']:,} bytes is refused, and the other pages still pass")


@check("banned-control")
def banned_control():
    if NAMES_FILE.exists():
        names, scratch = load_names(), ROOT / ".private"
    else:
        names, scratch = ["Examplecorp"], None
        print("  no private names list in this copy, so the scanner is tested on a stand-in name")
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        planted = Path(tmp) / "planted.txt"
        planted.write_text(f"a line that mentions {names[0].upper()} once\n", encoding="utf-8")
        hits = scan_for_names([planted], names)
    print(f"  planted one name in a scratch file: {len(hits)} hit(s)")
    require(len(hits) == 1, "control: the names scan missed a planted name")
    require(not find_names("embossed", ["boss"]), "control: the scan matched inside a longer word")


# ---- the made-up world ------------------------------------------------------------------------

def built_world(path):
    from staple import db, world
    conn = db.fresh(path)
    world.build(conn)
    return conn


def digest(conn):
    import hashlib
    h = hashlib.sha256()
    for table in ("jobs", "job_parts", "techs", "tech_skills", "truck_stock", "backorders"):
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2"):
            h.update(repr(row).encode())
    return h.hexdigest()


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


_WORLD = {}


def shared_world():
    """One freshly built world per run of check.py, shared by every check that only reads it."""
    if "conn" not in _WORLD:
        tmp = tempfile.mkdtemp(prefix="staple-check-")
        _WORLD["conn"] = built_world(Path(tmp) / "world.db")
        atexit.register(drop_shared_world, tmp)
    return _WORLD["conn"]


def drop_shared_world(tmp):
    """Close the shared world and remove its folder when the run ends; a folder that will not go is named."""
    _WORLD.pop("conn").close()
    try:
        shutil.rmtree(tmp)
    except OSError as err:
        print(f"WARN: the scratch folder {tmp} could not be removed ({err}); delete it by hand")


def shared_week():
    """The week's plan on the shared world, built once and read by the plan and report checks."""
    if "week" not in _WORLD:
        from staple import plan, triage
        conn = shared_world()
        results_written(conn)    # the plan's buyer list reads the back-order query
        _WORLD["followups"] = triage.load(conn)[1]
        _WORLD["week"] = plan.build(conn)
    return _WORLD["week"], _WORLD["followups"]


@check("predictor")
def predictor():
    from staple.predict import Predictor
    conn = shared_world()
    p = Predictor(conn)

    fam, problem, n = conn.execute("""SELECT family, booked_problem, COUNT(*) n FROM jobs WHERE split = 'train'
                                      GROUP BY 1, 2 ORDER BY n DESC LIMIT 1""").fetchone()
    appliance = problem.split(".")[0]
    raw = dict(conn.execute("""SELECT jp.part, COUNT(*) * 1.0 / ? FROM jobs j JOIN job_parts jp USING (job)
                               WHERE j.split = 'train' AND j.family = ? AND j.booked_problem = ?
                               GROUP BY jp.part""", (n, fam, problem)).fetchall())
    odds = p.odds(appliance, fam, problem)
    top = max(raw, key=lambda k: raw[k])
    print(f"  plenty of history: {fam} {problem}, {n} past jobs; {top.split('/')[1]} needed on "
          f"{raw[top]:.1%} of them; the predictor says {odds[top]:.1%}")
    require(abs(odds[top] - raw[top]) < 0.02, "with plenty of history the odds should match the plain share")

    n_t, c_t = p.history("type", appliance)
    n_p, c_p = p.history("problem", problem)
    n_f, c_f = p.history("family", (fam, problem))
    p_type = c_t[top] / n_t
    p_problem = (c_p[top] + 20 * p_type) / (n_p + 20)
    by_hand = (c_f[top] + 20 * p_problem) / (n_f + 20)
    require(abs(by_hand - odds[top]) < 1e-12, "the odds do not match the arithmetic written in the docstring")
    print("  the same number recomputed by hand from the three levels' counts: matches")

    thin_fam, thin_problem, thin_n = conn.execute("""SELECT j.family, j.booked_problem, COUNT(*) n FROM jobs j
        JOIN families f USING (family) WHERE j.split = 'train' AND f.first_day > '2026-01-01'
        GROUP BY 1, 2 ORDER BY n ASC, 1, 2 LIMIT 1""").fetchone()
    thin_type = thin_problem.split(".")[0]
    thin_odds = Predictor(conn).odds(thin_type, thin_fam, thin_problem)
    raw_only = Predictor(conn, pull=0).odds(thin_type, thin_fam, thin_problem)
    n_p2, c_p2 = p.history("problem", thin_problem)
    wider = {part: c_p2[part] / n_p2 for part in thin_odds}
    lean = sum(abs(thin_odds[x] - wider[x]) for x in thin_odds)
    no_lean = sum(abs(raw_only[x] - wider[x]) for x in thin_odds)
    print(f"  thin history: {thin_fam} {thin_problem}, {thin_n} past job(s); distance from the wider "
          f"group's odds {lean:.2f} with the pull, {no_lean:.2f} without")
    require(lean < no_lean, "thin history should lean toward the wider group")

    carry = p.carry_list(appliance, fam, problem, 4)
    require(len(carry) == 4 and all(odds[a] >= odds[b] for a, b in zip(carry, carry[1:])),
            "a carry list must hold k parts in falling order of odds")


@check("backtest")
def backtest_check():
    from staple import backtest
    r = backtest.run(shared_world())
    cov, k = r["coverage"], backtest.SHOWN_K
    print(f"  {r['jobs']:,} held-back jobs; coverage with {k} parts: predictor {cov['predictor'][k]:.1%}, "
          f"national top parts {cov['national top parts'][k]:.1%}, today's trucks {r['today']:.1%}")
    require(cov["predictor"][k] > cov["national top parts"][k] + 0.10, "the predictor does not beat the baseline")
    curve = [cov["predictor"][i] for i in backtest.KS]
    require(all(a <= b for a, b in zip(curve, curve[1:])), "coverage fell as the list got longer")
    newest = r["groups"]["newest model families"]
    print(f"  newest families: {newest['predictor'][1]:.1%} with the lean, "
          f"{newest['no lean on wider groups'][1]:.1%} without")
    require(newest["predictor"][1] > newest["no lean on wider groups"][1], "the lean does not help thin history")


@check("control")
def control():
    from staple import backtest
    r = backtest.run(shared_world())
    cov = r["coverage"]
    for k in range(1, 7):
        shuffled, base, real = (cov[m][k] for m in ("shuffled-history control", "national top parts", "predictor"))
        require(abs(shuffled - base) <= 0.03, f"with {k} parts the shuffled control sits {shuffled - base:+.1%} "
                                              "from the baseline: the gain may be an artefact")
        require(real - shuffled > 0.05 or k >= 6, f"with {k} parts the predictor barely beats its own control")
    print(f"  shuffled history scores {cov['shuffled-history control'][3]:.1%} with 3 parts against the "
          f"baseline's {cov['national top parts'][3]:.1%}: the gain comes from the history's links, not the method")


def load_faults(fleet, tech, parts, stuck, before):
    """What is wrong with one truck's load: over its space, a locked part without certification, a part loaded
    while no center could ship it, or a stuck part sent back. stuck is the parts no center can ship that day,
    before the truck's load the night before."""
    faults = []
    if sum(fleet.size[p] for p in parts) > fleet.capacity[tech]:
        faults.append("over its space")
    if tech not in fleet.certified and parts & fleet.locked:
        faults.append("a locked part on an uncertified truck")
    if any(p in stuck and p not in before for p in parts):
        faults.append("a back-ordered part the truck did not already hold")
    if any(p in stuck and p not in parts for p in before):
        faults.append("a back-ordered part taken off before it shipped")
    return faults


def faulty_loads(fleet, loads):
    """How many of one policy's recorded truck-days break a load rule, each truck followed in date order."""
    last, bad = {}, 0
    for (day, tech), parts in sorted(loads.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        bad += bool(load_faults(fleet, tech, parts, fleet.stuck(day), last.get(tech, fleet.stock[tech])))
        last[tech] = parts
    return bad


def extra_parts_at(rows, policy, target, base):
    """Extra parts a top-up needs to reach the target coverage, reading between list lengths from today's truck."""
    points = [(base["coverage"], 0.0)] + [(r["coverage"], r["parts"] - base["parts"])
                                          for r in sorted((r for r in rows if r["policy"] == policy), key=lambda r: r["k"])]
    for (c0, e0), (c1, e1) in zip(points, points[1:]):
        if c1 >= target:
            return e0 + (e1 - e0) * (target - c0) / (c1 - c0) if c1 > c0 else e1
    return float("inf")


def saving_ratio(rows, real, control, base):
    """At the best coverage both top-ups reach, the extra parts the real lists need as a share of the control's."""
    target = min(max(r["coverage"] for r in rows if r["policy"] == p) for p in (real, control))
    if target <= base["coverage"]:
        return target, 0.0, 0.0, 0.0
    a, b = extra_parts_at(rows, real, target, base), extra_parts_at(rows, control, target, base)
    return target, a, b, a / b


def say(row):
    per_job = f", {row['k']} per job" if row["k"] and "lists" in row["policy"] else ""
    print(f"  {row['policy']}{per_job}: covers {row['coverage']:.1%}, {row['parts']:.1f} parts "
          f"({row['permanent']:.1f} permanent), {row['moves']:.1f} moves, ${row['value']:,.0f} held, "
          f"${row['total']:.2f} a truck-day")


@check("dollars")
def dollars():
    from staple import backtest, load
    conn = shared_world()
    r = load.run(conn)
    jobs = r["jobs"]
    today, top, only, every, bal = (r["today"], r["best"][load.TOP_UP], r["best"][load.LISTS_ONLY],
                                    r["everything"], r["balanced"])
    for row in (today, every, top, only, bal):
        say(row)
    expected = sum(j["on_truck"] for j in jobs) / len(jobs)
    require(abs(today["coverage"] - expected) < 1e-12, "the replay of today's trucks disagrees with the history")

    fleet, lists = r["fleet"], r["lists"]
    recorded = load.replay(fleet, jobs, [("balanced", 0, load.balanced(fleet, lists, r["surprise"])),
                                         ("every part", every["k"], load.top_up(fleet, lists, every["k"])),
                                         ("lists only", 8, load.top_up(fleet, lists, 8, keep_stock=False))], record=True)
    faults = sum(faulty_loads(fleet, row["loads"]) for row in recorded)
    uncertified = next(t for t in fleet.capacity if t not in fleet.certified)
    planted = load_faults(fleet, uncertified, set(fleet.size), fleet.stuck(jobs[0]["day"]), fleet.stock[uncertified])
    print(f"  {faults} faulty loads in {sum(len(x['loads']) for x in recorded):,}; "
          f"a planted bad load shows {len(planted)} of 3 faults")
    require(faults == 0, f"{faults} loads break the space, lock or back-order rules")
    require(len(planted) == 3, "the load-fault check cannot see a planted bad load")

    a, b = sorted(fleet.cost)[:2]
    for load_, before, moves in (({a, b}, {a}, 1), ({a}, {b}, 2)):
        got = load.day_cost(fleet, load_, before, 2)
        want = (2 * load.REPEAT_VISIT, moves * load.MOVE,
                sum(fleet.cost[p] for p in load_) * load.HOLDING_RATE / 365)
        require(all(abs(g - w) < 1e-12 for g, w in zip(got, want)), f"day cost {got} is not the hand sum {want}")

    require(top["total"] < today["total"] and top["coverage"] > today["coverage"],
            "topping up with the day's lists does not beat today's trucks")
    print(f"  the top-up pays while a repeat visit costs more than ${r['break_even']:.2f}")
    require(only["total"] > top["total"], "dropping the fixed stock now beats keeping it: re-read the trade-off")
    require(bal["total"] < min(row["total"] for row in (today, every, top, only)),
            "the balanced truck costs more a day than another load: it loses on the cost it minimizes")

    target, mine, theirs, ratio = saving_ratio(r["rows"], load.TOP_UP, load.CONTROL, today)
    print(f"  to reach {target:.1%}, the real lists add {mine:.2f} parts to today's truck and shuffled history "
          f"adds {theirs:.2f} ({ratio:.0%} of them)")
    require(ratio <= 0.75, "the real lists do not need clearly fewer extra parts than shuffled history")
    other = load.ranked_lists(load.Predictor(conn, shuffled=True, seed=11), jobs)
    null = load.replay(fleet, jobs, [(load.CONTROL, k, load.top_up(fleet, r["shuffled"], k)) for k in load.KS] +
                       [("second shuffle", k, load.top_up(fleet, other, k)) for k in load.KS] +
                       [("second balanced", 0, load.balanced(fleet, other, r["surprise"]))])
    *_, null_ratio = saving_ratio(null, "second shuffle", load.CONTROL, today)
    print(f"  control: a second shuffled history needs {null_ratio:.0%} of the first one's extra parts")
    require(null_ratio > 0.75, "two shuffled histories pass the parts test: it cannot tell knowledge from chance")

    gain = r["control_balanced"]["total"] - bal["total"]
    noise = abs(null[-1]["total"] - r["control_balanced"]["total"])
    print(f"  balanced from shuffled history costs ${gain:.2f} a truck-day more; two shuffles differ by ${noise:.2f}")
    require(gain > 3 * noise, "the balanced truck's edge over shuffled history is within shuffle noise")

    av = r["availability"]
    print("  carry-list parts: " + ", ".join(f"{m} {s:.1%}" for m, s in av["marks"].items()))
    print(f"  {av['blocked']} jobs needed a part no truck nearby holds; the bookings' odds predicted "
          f"{av['expected']:.0f}")
    print(f"  back-order flag at {load.BACKORDER_FLAG:.0%} odds: {av['flagged']:,} of {av['jobs']:,} bookings, "
          f"catching {av['caught']} of the {av['blocked']} blocked jobs")
    require(abs(sum(av["marks"].values()) - 1) < 1e-9, "the availability marks do not add up")
    require(av["caught"] <= min(av["flagged"], av["blocked"]), "the flag counts more catches than flags")
    require(abs(av["expected"] - av["blocked"]) <= 0.25 * av["blocked"],
            "the odds of a stuck part do not predict how many jobs were blocked")


def calibrated(actual, predicted):
    """A count lands within three Poisson spreads of its forecast."""
    return abs(actual - predicted) <= 3 * predicted ** 0.5


def calendar_faults(rows, fleet, days):
    """The parts whose listing does not follow the calendar. A part must be listed on every held-back day from a
    back-order's start to the day before it ships, with the days left falling by one a day, and on no other day."""
    listed, want = defaultdict(dict), defaultdict(dict)
    for r in rows:
        listed[r["part"]][r["day"]] = r["days_left"]
    for part, starts, ships in fleet.backorders:
        last = datetime.date.fromisoformat(ships)
        want[part].update({d: (last - datetime.date.fromisoformat(d)).days for d in days if starts <= d < ships})
    return sorted(part for part in set(listed) | set(want) if listed[part] != want[part])


@check("backorders")
def backorders_check():
    from staple import backorders, load
    conn = shared_world()
    fleet = load.Fleet(conn)
    rows = backorders.run(conn)
    days = sorted({r["day"] for r in rows})
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    sizes = [len(by_day[d]) for d in days]
    print(f"  {len(fleet.backorders)} back-orders across {len(days)} held-back days: {min(sizes)} to {max(sizes)} parts stuck "
          f"on a day, {len(rows):,} part-days listed")
    for r in by_day[days[-1]][:3]:
        print(f"  {r['day']}, {r['part']}: ships in {r['days_left']} days; {len(r['holders'])} trucks stock one; "
              f"{r['expected']:.1f} calls expected, {r['stuck']:.1f} stuck (${r['at_risk']:,.0f} at risk)")
    require(all(sorted(r["part"] for r in by_day[d]) == sorted(fleet.stuck(d)) for d in days),
            "a day's list does not hold every back-ordered part once")
    require(all(a["at_risk"] >= b["at_risk"] for d in days for a, b in zip(by_day[d], by_day[d][1:])),
            "a day's list is not ranked by dollars at risk")

    parts = {part for part, _, _ in fleet.backorders}
    off = calendar_faults(rows, fleet, days)
    print(f"  parts listed on every day from a back-order's start to the day before it ships, the days left falling by "
          f"one a day, and on no other day: {len(parts) - len(off)} of {len(parts)}")
    require(not off, f"the list does not follow the calendar for {off[:3]}")
    middle = next(r for r in rows if r["days_left"] > 1 and not r["first"])
    ship_day = str(datetime.date.fromisoformat(middle["day"]) + datetime.timedelta(days=middle["days_left"]))
    planted = {"a count that does not fall": [dict(r, days_left=r["days_left"] + 1) if r is middle else r for r in rows],
               "a day left out": [r for r in rows if r is not middle],
               "a part still listed the day it ships": rows + [dict(middle, day=ship_day, days_left=0)]}
    missed = [kind for kind, changed in planted.items() if calendar_faults(changed, fleet, days) != [middle["part"]]]
    print(f"  control: {', '.join(planted)}: {len(planted) - len(missed)} of {len(planted)} refused")
    require(not missed, f"the calendar check passed a planted fault: {missed}")

    scored = [r for r in rows if r["first"] and r["whole"]]
    after = str(datetime.date.fromisoformat(days[-1]) + datetime.timedelta(days=1))
    inside = [b for b in fleet.backorders if days[0] < b[2] <= after]
    print(f"  the forecast is scored once on each of the {len(scored)} back-orders that shipped inside the held-back days")
    require(len(scored) == len(inside), "a back-order that shipped inside the held-back days is scored twice or not at all")
    for name, actual, predicted in (("calls", sum(r["actual"] for r in scored), sum(r["expected"] for r in scored)),
                                    ("stuck calls", sum(r["actual_stuck"] for r in scored), sum(r["stuck"] for r in scored))):
        print(f"  {name} before the part shipped: forecast {predicted:.1f}, actual {actual}; "
              f"a forecast three times too high {'passes' if calibrated(actual, 3 * predicted) else 'fails'}")
        require(calibrated(actual, predicted), f"the forecast of {name} misses by more than three spreads")
        require(not calibrated(actual, 3 * predicted), f"the {name} test cannot tell a forecast three times too high")


def stand_in_api(reply):
    """A local stand-in for the Claude API that records the request it gets and returns a canned reply."""
    import http.server
    import json
    import threading
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
            seen["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/v1/messages", seen


@check("triage")
def triage_check():
    import json
    from staple import backtest, triage, world
    conn = shared_world()
    cache = json.loads(triage.CACHE.read_text(encoding="utf-8"))
    asked = json.loads(triage.FOLLOWUP_CACHE.read_text(encoding="utf-8"))
    require(cache["prompt_id"] == triage.PROMPT_ID and asked["prompt_id"] == triage.FOLLOWUP_ID,
            "a cache came from a different prompt: rebuild it")
    phrases, unsure = triage.phrases_of(conn), triage.unsure_phrases(cache["answers"])
    missing = sum(len(set(v) - set(cache["answers"].get(a, {}))) for a, v in phrases.items())
    unasked = sum(len(set(v) - set(asked["answers"].get(a, {}))) for a, v in unsure.items())
    print(f"  {sum(len(v) for v in cache['answers'].values())} cached answers from {cache['model']}; "
          f"{missing} of the world's {sum(len(v) for v in phrases.values())} phrases unanswered; "
          f"{unasked} of {sum(len(v) for v in unsure.values())} unsure phrases without a follow-up question")
    require(missing == 0 and unasked == 0, "a customer phrase has no cached answer or follow-up question")

    jobs = backtest.test_jobs(conn)
    r = triage.run(conn, cache["answers"], asked["answers"])
    rows, (n_sure, sure), (n_unsure, unsure) = r["rows"], r["calibration"]["sure"], r["calibration"]["unsure"]
    for name in ("booked code", "Claude", "Claude, top guess only", "Claude, asking when unsure",
                 "Claude, asking when unsure, random replies (control)", "keyword rules", "no code",
                 "Claude answers shuffled (control)"):
        row = rows[name]
        print(f"  {name}: agrees with the booked code {row['agree_booked']:.1%}, true problem "
              f"{row['agree_true']:.1%}, 3-part list covers {row['coverage']:.1%}")
    claude, rules = rows["Claude"], rows["keyword rules"]
    require(claude["agree_booked"] > rules["agree_booked"] + 0.10 and claude["coverage"] > rules["coverage"],
            "Claude does not clearly beat the keyword rules")
    require(rows["Claude answers shuffled (control)"]["coverage"] <= rows["no code"]["coverage"] + 0.03,
            "shuffled answers still help: the coverage gain may not come from reading the words")

    vague = [j for j in jobs if any(v in j["words"] for v in world.VAGUE)]
    said = [j for j in jobs if not any(v in j["words"] for v in world.VAGUE)]
    for label, group in (("words that describe the problem", said), ("words that describe nothing", vague)):
        g = triage.score(conn, group, cache["answers"], asked["answers"])["rows"]
        print(f"  {label} ({len(group):,} jobs): Claude agrees with the booked code {g['Claude']['agree_booked']:.1%}; "
              f"coverage {g['Claude']['coverage']:.1%} against the booked code's {g['booked code']['coverage']:.1%}")
        if group is said:
            require(g["Claude"]["agree_booked"] >= 0.95, "Claude misreads words that do describe the problem")
    print(f"  confidence: {n_sure:,} sure answers {sure:.1%} right; {n_unsure:,} unsure answers {unsure:.1%} right")
    require(sure > unsure + 0.2, "Claude's confidence does not separate right answers from wrong ones")
    gain = rows["Claude, asking when unsure"]["coverage"] - claude["coverage"]
    noise = rows["Claude, asking when unsure, random replies (control)"]["coverage"] - claude["coverage"]
    print(f"  follow-up asked on {r['asked']:,} bookings, the customer's problem offered on {r['settled']:,}; "
          f"coverage {gain:+.1%} with honest replies, {noise:+.1%} with random ones")
    require(gain > 0 and gain > 2 * noise, "the follow-up question's gain does not come from the customer's reply")
    bad_followups = 0
    for bad in ('[{"i": 0, "question": "Leak?", "answers": [{"text": "yes", "code": "LEAK"}]}]',
                '[{"i": 0, "question": "Which?", "answers": [{"text": "a", "code": "LEAK"}, {"text": "b", "code": "LEAK"}]}]',
                '[{"i": 0, "question": "Which?", "answers": [{"text": "a", "code": "LEAK"}, {"text": "b", "code": "ICE"}]}]'):
        try:
            triage.parse_followups(bad, [("FF_WARM", "Fresh-food side warm"), ("LEAK", "Leaking water")], ["it drips"])
        except (ValueError, KeyError):
            bad_followups += 1
    print(f"  {bad_followups} of 3 malformed follow-ups rejected")
    require(bad_followups == 3, "a malformed follow-up question gets through")

    problems = [("FF_WARM", "Fresh-food side warm"), ("LEAK", "Leaking water")]
    good = '[{"i": 0, "code": "LEAK", "confidence": 0.9}]'
    url, seen = stand_in_api({"content": [{"type": "text", "text": good}], "stop_reason": "end_turn"})
    got = triage.parse(triage.ask_api("a prompt", "test-key", url=url), problems, ["it drips"])
    require(seen["headers"].get("x-api-key") == "test-key" and seen["headers"].get("anthropic-version") == triage.API_VERSION
            and seen["body"]["model"] == triage.MODEL and seen["body"]["output_config"] == {"effort": triage.EFFORT},
            "live mode does not send the documented request")
    require(got == {"it drips": {"code": "LEAK", "confidence": 0.9}}, "live mode misreads a reply")
    refused = False
    url, _ = stand_in_api({"content": [], "stop_reason": "refusal", "stop_details": {"category": None}})
    try:
        triage.ask_api("a prompt", "test-key", url=url)
    except RuntimeError:
        refused = True
    rejected = 0
    for bad in ('[{"i": 0, "code": "ICE", "confidence": 0.9}]', "[]", "no array here"):
        try:
            triage.parse(bad, problems, ["it drips"])
        except (ValueError, KeyError):
            rejected += 1
    print(f"  live mode against a local stand-in: documented request sent, reply read; refusal raised: {refused}; "
          f"{rejected} of 3 malformed replies rejected")
    require(refused and rejected == 3, "live mode lets a refusal or a malformed reply through")


def visit_faults(result, jobs):
    """What breaks the day's rules: a call served twice or lost, a visit by a sick technician, a broken truck
    working after its breakdown, a route running past 17:00, and a call started before the minute it came in or
    the breakdown that moved it. The last is read from the day's own events, not from the replay's notes."""
    from staple import dispatch
    faults = Counter()
    served = Counter(job for _, _, job, _, _, _ in result["visits"])
    rolled = Counter(result["rolled_jobs"])
    faults["served or rolled other than once"] = sum(1 for j in jobs if served[j["job"]] + rolled[j["job"]] != 1)
    for day, tech, job, start, end, _ in result["visits"]:
        sick, broken = result["events"][day]
        rec = result["days"][day]
        faults["visit by a sick technician"] += tech in sick
        faults["visit on a broken truck after it broke"] += tech in broken and start >= broken[tech]
        faults["visit past 17:00"] += end > dispatch.DAY + 1e-9
        came = max([rec["arrive"].get(job, 0.0)] + [rec["broken"][t] for t, why in rec["moved"].get(job, ()) if why == "breakdown"])
        faults["a call started before it came in or was moved"] += start < came - 1e-9
    return +faults


@check("dispatch")
def dispatch_check():
    from staple import backtest, dispatch
    conn = shared_world()
    out = dispatch.run(conn)
    for name, r in out.items():
        print(f"  {name}: first-visit fix {r['fixes_per_call']:.1%} of calls; parts missing {r['parts_missing']:.1%}; "
              f"{r['rolled']} rolled to tomorrow; {r['drive_per_call']:.1f} drive minutes a call; ${r['cost_per_call']:.2f} a call")
    today, loaded, skills, parts, staple = (out[name] for name, *_ in dispatch.POLICIES)
    print(f"  {staple['sick']} sick days and {staple['broken']} breakdowns moved {staple['moved']} calls; "
          f"those calls were fixed first time {staple['moved_fix_rate']:.1%} against {staple['fix_rate']:.1%} overall")
    require(loaded["parts_missing"] < today["parts_missing"], "balanced loads do not beat today's stock under surprises")
    require(staple["fixes_per_call"] > loaded["fixes_per_call"] > today["fixes_per_call"],
            "Staple's dispatch does not beat sending the nearest technician")
    require(staple["fixes_per_call"] >= max(skills["fixes_per_call"], parts["fixes_per_call"]),
            "knowing both skills and parts does worse than knowing one")
    require(staple["cost_per_call"] < loaded["cost_per_call"], "the extra driving costs more than the fixes it buys")
    require(staple["cost_per_call"] <= min(skills["cost_per_call"], parts["cost_per_call"]),
            "the rule that knows most loses on its own cost: the day is planned worse, not chosen worse")

    jobs = backtest.test_jobs(conn)
    from staple import load
    load.mark_unplanned(jobs)
    recorded = dispatch.replay(conn, jobs, "skills and parts", "balanced", record=True)
    faults = visit_faults(recorded, jobs)
    day = next(iter(recorded["events"]))
    planted = dict(recorded, visits=recorded["visits"] + [(day, "T99", jobs[0]["job"], 600.0, 700.0, True)],
                   events=dict(recorded["events"], **{day: ({"T99"}, {"T99": 0.0})}))
    caught = visit_faults(planted, jobs)
    print(f"  rule breaks in {len(recorded['visits']):,} visits: {sum(faults.values())}; "
          f"a planted visit shows {len(caught)} of 4 kinds")
    require(not faults, f"the replay breaks the day's rules: {dict(faults)}")
    require(len(caught) == 4, f"the rule check misses a planted break: it saw {dict(caught)}")

    figures = [k for k, v in staple.items() if isinstance(v, (int, float))]
    moved = [k for k in figures if recorded[k] != staple[k]]
    print(f"  recording the plan: {len(figures) - len(moved)} of {len(figures)} figures unchanged against the unrecorded run")
    require(not moved, f"recording the plan changed the replay's results: {moved}")
    require([k for k in figures if loaded[k] != staple[k]], "control: the comparison saw no difference between two policies")


@check("dispatch-control")
def dispatch_control():
    from staple import backtest, dispatch, load
    conn = shared_world()
    out = dispatch.run(conn)
    jobs = backtest.test_jobs(conn)
    load.mark_unplanned(jobs)
    same = dispatch.replay(conn, jobs, "skills and parts", "balanced", dispatch.Crew(conn))
    base, parts, staple = (out[n] for n in ("nearest technician, balanced loads", "parts only, balanced loads",
                                            "Staple: skills and parts, balanced loads"))
    skills_off, odds_off = out[dispatch.SHUFFLED_SKILLS], out[dispatch.SHUFFLED_ODDS]
    print(f"  first-visit fix a call: nearest {base['fixes_per_call']:.1%}, parts only {parts['fixes_per_call']:.1%}, "
          f"Staple {staple['fixes_per_call']:.1%}")
    print(f"  shuffled skills {skills_off['fixes_per_call']:.1%}: the skill gain over parts only "
          f"({staple['fixes_per_call'] - parts['fixes_per_call']:+.1%}) becomes "
          f"{skills_off['fixes_per_call'] - parts['fixes_per_call']:+.1%}")
    require(abs(same["fixes_per_call"] - staple["fixes_per_call"]) < 1e-12, "the control's machinery changes the answer by itself")

    # Why the parts-odds control does not fall back, measured and not only said. The odds are handed to the
    # dispatcher in three cut-down forms: knowing only the appliance, for same-day calls alone, for booked calls alone.
    predictor = load.Predictor(conn)
    lists = load.ranked_lists(predictor, jobs)
    by_type = {}
    for kind in {j["type"] for j in jobs}:
        n, counts = predictor.history("type", kind)
        by_type[kind] = sorted(((p, counts[p] / n) for p in predictor.parts_of[kind]), key=lambda kv: (-kv[1], kv[0]))
    cut = {"appliance only": {j["job"]: by_type[j["type"]] for j in jobs},
           "same-day calls only": {j["job"]: lists[j["job"]] if j["unplanned"] else [] for j in jobs},
           "booked calls only": {j["job"]: [] if j["unplanned"] else lists[j["job"]] for j in jobs}}
    got = {name: dispatch.replay(conn, jobs, "skills and parts", "balanced", part_odds=odds)["fixes_per_call"]
           for name, odds in cut.items()}
    none = out["skills only, balanced loads"]["fixes_per_call"]
    gain = staple["fixes_per_call"] - none
    print(f"  shuffled-history parts odds {odds_off['fixes_per_call']:.1%}, against {none:.1%} with no parts judgment and "
          f"{got['appliance only']:.1%} with odds that know only the appliance")
    print(f"  of the parts judgment's {gain:+.1%}: {(got['same-day calls only'] - none) / gain:.0%} is kept with odds "
          f"for same-day calls alone, {(got['booked calls only'] - none) / gain:.0%} with odds for booked calls alone")
    require(abs(got["appliance only"] - odds_off["fixes_per_call"]) < 0.005,
            "appliance-level odds no longer match the shuffled ones: the report's reason for this control is wrong")
    require(abs(none - odds_off["fixes_per_call"]) >= 0.005,
            "control: no parts judgment at all sits within half a point of the shuffled odds, so that test cannot fail")
    require(got["same-day calls only"] - none > 0.5 * gain,
            "same-day calls no longer carry most of the parts judgment's gain: rewrite the report's reason")
    require(got["booked calls only"] - none < 0.5 * gain,
            "control: booked calls alone also keep most of the gain, so the split tells nothing")
    side = max(c["rolled"] for c in (skills_off, odds_off)) - staple["rolled"]
    print(f"  the controls roll {side:+} calls beyond Staple's, so neither wins by overbooking the day")
    require(side <= 0.01 * staple["calls"], "a control withdraws the gain by overbooking routes, not by choosing worse")
    require(skills_off["fixes_per_call"] - parts["fixes_per_call"] <= 0.5 * (staple["fixes_per_call"] - parts["fixes_per_call"]),
            "shuffled skills keep most of the skill gain: it may not come from knowing the technicians")
    require(abs(odds_off["fixes_per_call"] - staple["fixes_per_call"]) < 0.01,
            "model-level parts odds now matter to dispatch: re-read the claim that appliance-level odds suffice")


def who_went(result):
    """Every visit of a recorded replay without its outcome: the day, the technician, the call and its times."""
    return [(day, tech, job, round(start, 6), round(end, 6)) for day, tech, job, start, end, _ in result["visits"]]


@check("dispatch-blind")
def dispatch_blind():
    """The dispatcher's view of a technician comes from the history alone. The table the made-up world was written
    from is scrambled after the history exists, and every visit must stay where it was."""
    import random
    import sqlite3
    from staple import backtest, dispatch, load
    conn = shared_world()
    crew = dispatch.Crew(conn)
    gap = max(abs(crew.belief[k][0] - crew.skill[k][0]) for k in crew.skill)
    print(f"  {len(crew.skill)} technician and appliance pairs, each rate learned from at least {min(crew.seen.values()):,} "
          f"training visits; every learned rate sits within {gap:.3f} of the rate the history was written with")
    require(gap < 0.1, "a learned success rate sits far from the rate the history was written with")

    # A thin record, worked by hand: a technician left with five visits must be pulled toward the appliance's average.
    thin = sqlite3.connect(":memory:")
    conn.backup(thin)
    tech, kind = pair = min(crew.skill)
    keep = "SELECT job FROM jobs WHERE split = 'train' AND parts_on_truck = 1 AND tech = ? AND type = ? ORDER BY job LIMIT 5"
    thin.execute(f"DELETE FROM jobs WHERE split = 'train' AND tech = ? AND type = ? AND job NOT IN ({keep})", pair + pair)
    own = thin.execute("SELECT COUNT(*), SUM(first_visit_fix) FROM jobs WHERE split = 'train' AND parts_on_truck = 1 "
                       "AND tech = ? AND type = ?", pair).fetchone()
    group = thin.execute("SELECT COUNT(*), SUM(first_visit_fix) FROM jobs WHERE split = 'train' AND parts_on_truck = 1 "
                         "AND type = ?", (kind,)).fetchone()
    by_hand = (own[1] + dispatch.SKILL_PULL * group[1] / group[0]) / (own[0] + dispatch.SKILL_PULL)
    got = dispatch.Crew(thin).belief[pair][0]
    print(f"  {tech} left with {own[0]} {kind} visits, {own[1]} of them fixed: the appliance's average is "
          f"{group[1] / group[0]:.1%}, the learned rate {got:.1%}, and by hand {by_hand:.1%}")
    require(own[0] == 5 and abs(got - by_hand) < 1e-12, "the learned rate is not the arithmetic its docstring states")
    require(abs(got - group[1] / group[0]) < abs(got - own[1] / own[0]) or own[1] / own[0] == group[1] / group[0],
            "a technician with five visits is not pulled toward the appliance's average")
    thin.close()

    jobs = backtest.test_jobs(conn)
    load.mark_unplanned(jobs)
    before = dispatch.replay(conn, jobs, "skills and parts", "balanced", crew, record=True)

    scrambled = sqlite3.connect(":memory:")
    conn.backup(scrambled)
    rows = scrambled.execute("SELECT tech, type, success_rate FROM tech_skills ORDER BY tech, type").fetchall()
    rates = [rate for _, _, rate in rows]
    random.Random(1).shuffle(rates)
    scrambled.executemany("UPDATE tech_skills SET success_rate = ? WHERE tech = ? AND type = ?",
                          [(rate, t, k) for (t, k, _), rate in zip(rows, rates)])
    changed = sum(old != new for (_, _, old), new in zip(rows, rates))
    after = dispatch.replay(scrambled, jobs, "skills and parts", "balanced", dispatch.Crew(scrambled), record=True)
    same = len(set(who_went(before)) & set(who_went(after)))
    print(f"  the world's skill table scrambled after the history was written, {changed} of {len(rows)} rates changed: "
          f"{same:,} of {len(before['visits']):,} visits stay with the same technician at the same time")
    print(f"  the scrambled world then fixes {after['fixes_per_call']:.1%} of calls against {before['fixes_per_call']:.1%}, "
          f"because the world changed under a plan that did not")
    require(changed > len(rows) / 2, "the scramble left most rates where they were, so it tests little")
    require(who_went(before) == who_went(after), "scrambling the world's skill table moved a visit, so dispatch reads it")
    scrambled.close()

    # Control: the same comparison must see a plan move when the history itself says something else.
    rewritten = sqlite3.connect(":memory:")
    conn.backup(rewritten)
    best = max(crew.belief, key=lambda k: (crew.belief[k][0], k))
    rewritten.execute("UPDATE jobs SET first_visit_fix = 0 WHERE split = 'train' AND tech = ? AND type = ?", best)
    misled = dispatch.Crew(rewritten)
    rewritten.close()
    moved = dispatch.replay(conn, jobs, "skills and parts", "balanced", misled, record=True)
    apart = len(set(who_went(before)) ^ set(who_went(moved)))
    print(f"  control: a history rewritten so {best[0]} never fixed a {best[1]} drops their learned rate from "
          f"{crew.belief[best][0]:.1%} to {misled.belief[best][0]:.1%} and moves {apart:,} visits")
    require(misled.belief[best][0] < 0.1 and apart, "a rewritten history moved no visit, so the comparison sees nothing")


def calendar_load_faults(recorded, fleet):
    """Where a recorded replay's nightly loads break the back-order calendar: a day replayed on the wrong stuck
    parts, a stuck part loaded while no center could ship it, or one sent back before it shipped."""
    faults = Counter()
    for day, rec in recorded["days"].items():
        faults["a day replayed on the wrong back-orders"] += rec["stuck"] != fleet.stuck(day)
        for tech, cargo in rec["load"].items():
            found = load_faults(fleet, tech, cargo, fleet.stuck(day), rec["before"][tech])
            faults.update(f for f in found if "back-ordered" in f)
    return +faults


@check("replay-clock")
def replay_clock():
    """The replay obeys the clock and the calendar. No call starts before the minute it came in or the breakdown
    that moved it; a back-ordered part is never loaded until it ships and is loadable again after; and the lists
    people read before 08:00 hold only what is known by then."""
    import copy
    from staple import backtest, dispatch, load, metrics, plan
    conn = shared_world()
    results_written(conn)
    fleet = load.Fleet(conn)
    jobs = backtest.test_jobs(conn)
    load.mark_unplanned(jobs)
    recorded = dispatch.replay(conn, jobs, "skills and parts", "balanced", record=True)

    # The clock: read from the day's own events, never from what the replay noted for itself.
    late = "a call started before it came in or was moved"
    faults = visit_faults(recorded, jobs)
    start_of = {(day, job): start for day, _, job, start, _, _ in recorded["visits"]}
    came_in = [(day, job, at) for day, rec in recorded["days"].items() for job, at in rec["arrive"].items()
               if (day, job) in start_of]
    broke = [(day, job, max(rec["broken"][t] for t, why in origins if why == "breakdown"))
             for day, rec in recorded["days"].items() for job, origins in rec["moved"].items()
             if (day, job) in start_of and any(why == "breakdown" for _, why in origins)]
    tight = min(start_of[(day, job)] - at for day, job, at in came_in + broke)
    print(f"  {len(came_in):,} calls that came in during the day and {len(broke)} moved by a breakdown: "
          f"{faults.get(late, 0)} started before that minute; the closest started {tight:.1f} minutes after it")
    require(not faults, f"the replay breaks the day's rules: {dict(faults)}")
    require(came_in and broke, "control: the replay holds no same-day call or no breakdown move to test")
    for what, (day, job, at) in (("a same-day call", came_in[0]), ("a call moved by a breakdown", broke[0])):
        early = dict(recorded, visits=[(d, t, j, at - 1.0, e, c) if (d, j) == (day, job) else (d, t, j, s, e, c)
                                       for d, t, j, s, e, c in recorded["visits"]])
        require(late in visit_faults(early, jobs), f"control: {what} started a minute early was not refused")
    print("  control: a same-day call and a breakdown-moved call, each started one minute early, are both refused")

    # The calendar.
    broken = calendar_load_faults(recorded, fleet)
    nights = sum(len(rec["load"]) for rec in recorded["days"].values())
    stuck_days = sum(len(rec["stuck"]) for rec in recorded["days"].values())
    print(f"  {nights:,} truck loads over {stuck_days:,} stuck part-days: {sum(broken.values())} loads take on a part no "
          f"center could ship, or send one back before it ships")
    require(not broken, f"a load breaks the back-order calendar: {dict(broken)}")
    days = sorted(recorded["days"])
    again = 0
    shipped = [(part, ships) for part, _, ships in fleet.backorders if days[0] < ships <= days[-1]]
    for part, ships in shipped:
        week_after = [d for d in days if ships <= d][:7]
        again += any(part in recorded["days"][d]["load"][t] - recorded["days"][d]["before"][t]
                     for d in week_after for t in recorded["days"][d]["load"])
    print(f"  of the {len(shipped)} back-orders that shipped inside the held-back days, {again} had the part put on a "
          f"truck within a week of shipping")
    require(again, "no part was loaded after its back-order ended, so the calendar never clears one")
    day = next(d for d in days if recorded["days"][d]["stuck"])
    rec = recorded["days"][day]
    tech, part = next((t, p) for t in sorted(rec["load"]) for p in sorted(rec["stuck"])
                      if p not in rec["before"][t] and p not in fleet.locked)
    planted = dict(recorded, days=dict(recorded["days"], **{day: dict(rec, load=dict(rec["load"], **{tech: rec["load"][tech] | {part}}))}))
    require("a back-ordered part the truck did not already hold" in calendar_load_faults(planted, fleet),
            "control: a stuck part planted on a truck that did not hold it was not refused")
    print(f"  control: {part}, stuck on {day}, planted on {tech}'s truck, is refused")

    # The lists people read before 08:00, and the buyer's list.
    week, followups = shared_week()
    kinds = ("a list read before 08:00 naming a call that has not come in",
             "a buyer's list that is not the back-order query's rows", "technicians with no booked calls not named")
    found = plan_faults(week, followups)
    by_day = defaultdict(list)
    for row in metrics.read(conn, "backorder_exposure"):
        by_day[row["day"]].append(row)
    all_days = sorted(by_day)
    changed = sum({(r["part"], r["ships_in_days"]) for r in by_day[a]} != {(r["part"], r["ships_in_days"]) for r in by_day[b]}
                  for a, b in zip(all_days, all_days[1:]))
    steps = [(r["ships_in_days"], n["ships_in_days"]) for a, b in zip(all_days, all_days[1:])
             for r in by_day[a] for n in by_day[b] if n["part"] == r["part"] and r["ships_in_days"] > 1]
    buyer = sum(i["owner"] == "Buyer" for d in week["days"] for i in d["todo"])
    idle = [len(i["idle"]) for d in week["days"] for i in d["todo"] if "idle" in i]
    unplanned = sum(j["unplanned"] for j in week["jobs"])
    print(f"  the week's lists: {buyer} buyer's items, each the back-order query's own row for its day; "
          f"{unplanned} same-day calls, none on a list read before 08:00; "
          f"{min(idle)} to {max(idle)} technicians a day with no booked calls, all named" if idle else
          f"  the week's lists: {buyer} buyer's items; {unplanned} same-day calls; every technician has a booked call")
    print(f"  the back-order list differs from the day before on {changed} of {len(all_days) - 1} days, and a part "
          f"listed two days running has one day fewer left on {sum(a - 1 == b for a, b in steps)} of {len(steps)}")
    require(not any(k in found for k in kinds), f"the week's lists break their rules: {dict(found)}")
    require(changed and steps and all(a - 1 == b for a, b in steps), "the buyer's list does not count down day by day")

    d = week["days"][0]
    late_job = next(j["job"] for j in week["jobs"] if j["unplanned"] and j["day"] == d["day"])
    item = next(i for i in d["todo"] if i["owner"] == "Buyer")
    named = next(i for i in d["todo"] if "idle" in i)

    def with_todo(todo):
        out = copy.copy(week)
        out["days"] = [dict(d, todo=todo)] + week["days"][1:]
        return out

    planted = {
        kinds[0]: with_todo(d["todo"] + [{"owner": "Call center", "when": "before 08:00", "tech": None, "job": late_job,
                                          "text": "", "detail": ""}]),
        kinds[1]: with_todo([dict(i, row=dict(i["row"], dollars_at_risk=i["row"]["dollars_at_risk"] + 1)) if i is item else i
                             for i in d["todo"]]),
        kinds[2]: with_todo([dict(i, idle=i["idle"][1:]) if i is named else i for i in d["todo"]]),
    }
    missed = [kind for kind, changed_week in planted.items() if kind not in plan_faults(changed_week, followups)]
    print(f"  control: a same-day call on a morning list, a buyer's figure off by a dollar, and an idle technician left "
          f"out: {len(planted) - len(missed)} of {len(planted)} refused")
    require(not missed, f"the list rules passed a planted fault: {missed}")


def plan_faults(week, followups):
    """What breaks the week's plan: the day's rules, the load rules, a load its parts' worth does not
    reproduce, a call off Claude's code, an unsure call without its question, a morning that drops a sick call,
    a breakdown or a moved call's from and to, and day totals that disagree with the replay's own."""
    from staple import load, metrics, plan, triage
    ctx, faults = week["ctx"], Counter()
    by_id = {j["job"]: j for j in week["jobs"]}
    listed = defaultdict(list)
    for row in metrics.read(ctx.conn, "backorder_exposure"):
        listed[row["day"]].append(row)
    for result in (week["ours"], week["base"]):
        faults.update(visit_faults(result, week["jobs"]))
    for j in week["jobs"]:
        a = j["ai"]
        unsure = a["first_confidence"] < triage.BLEND_BELOW and j["words"] in followups.get(j["type"], {})
        faults["a call not on Claude's code"] += j["problem"] != f"{j['type']}.{a['code']}"
        faults["an unsure call without its question"] += unsure != bool(a["question"])
        faults["a reading the question did not produce"] += a["code"] != (a["reply"] or a["first"])
    for d in week["days"]:
        rec, m = week["ours"]["days"][d["day"]], d["morning"]
        for tech, cargo in rec["load"].items():
            faults.update(load_faults(ctx.fleet, tech, cargo, rec["stuck"], rec["before"][tech]))
            worth = d["trucks"][tech]["sheet"]["worth"]
            again = load.fill(ctx.fleet, tech, set(), {p: v / ctx.fleet.size[p] for p, v in worth.items() if v > 0},
                              rec["stuck"], rec["before"][tech])
            faults["a load its parts' worth does not reproduce"] += again != cargo
        faults["a list read before 08:00 naming a call that has not come in"] += sum(
            1 for i in d["todo"] if i["when"] == "before 08:00" and i["job"] is not None and by_id[i["job"]]["unplanned"])
        costliest = sorted(listed[d["day"]], key=lambda r: (-r["dollars_at_risk"], r["part"]))[:plan.BUYER_ITEMS]
        buyer = [i for i in d["todo"] if i["owner"] == "Buyer"]
        faults["a buyer's list that is not the back-order query's rows"] += (
            [i.get("row") for i in buyer] != costliest
            or any(f"{r['expected_calls']:.1f} calls" not in i["text"] or f"${r['dollars_at_risk']:,.0f} " not in i["detail"]
                   or f"Ships in {r['ships_in_days']} day" not in i["detail"] for i, r in zip(buyer, costliest)))
        free = sorted(t for t, truck in d["trucks"].items() if not truck["calls"])
        named = [i["idle"] for i in d["todo"] if "idle" in i]
        faults["technicians with no booked calls not named"] += (
            named != ([free] if free else []) or any(t not in i["text"] for i in d["todo"] if "idle" in i for t in free))
        faults["a sick call or breakdown missing from the morning"] += (
            set(m["sick"]) != rec["sick"] or m["broken"] != {t: at for t, at in rec["broken"].items() if t not in rec["sick"]})
        ran = {i: t for _, t, i, _, _, _ in rec["visits"]}
        shown = {x["job"]: x for x in m["moved"]}
        faults["a moved call without its from and to"] += sum(
            1 for jid, origins in rec["moved"].items()
            if jid not in shown or shown[jid]["from"] != origins[0][0] or shown[jid]["to"] != ran.get(jid))
    for name in ("ours", "base"):
        served = sum(d[name]["served"] for d in week["days"])
        fixes = sum(d[name]["fixes"] for d in week["days"])
        r = week[name]
        faults["day totals that disagree with the replay"] += (
            served != r["served"] or abs(fixes - r["fixes_per_call"] * r["calls"]) > 1e-6)
    return +faults


@check("plan")
def plan_check():
    import copy
    from staple import backtest, dispatch, load, plan
    conn = shared_world()
    week, followups = shared_week()
    for d in week["days"]:
        o, b, m, a = d["ours"], d["base"], d["morning"], d["ai"]
        owners = Counter(i["owner"] for i in d["todo"])
        print(f"  {d['day']}: {int(o['calls'])} calls; first-visit fix {o['fixes'] / o['calls']:.1%} against "
              f"{b['fixes'] / b['calls']:.1%} for the nearest technician on today's stock; Claude asked {a['asked']}, "
              f"settled {a['settled']}; sick {m['sick'] or 'none'}, breakdowns "
              f"{ {t: plan.clock(at) for t, at in m['broken'].items()} or 'none'}, {len(m['moved'])} calls moved; "
              f"to-do {dict(owners)}")
    print(f"  the page opens on {week['open']}, the most recent day on which a sick call and a breakdown both moved calls")
    faults = plan_faults(week, followups)
    print(f"  plan faults over {len(week['jobs']):,} calls and {len(week['days']) * len(week['ctx'].crew.techs)} truck-days: "
          f"{dict(faults) or 'none'}")
    require(not faults, f"the week's plan breaks its rules: {dict(faults)}")
    opening = [d["day"] for d in week["days"] if plan.opens_on(d)]
    require(opening and week["open"] == opening[-1], "the page does not open on the most recent day whose sick call and breakdown both moved calls")
    moot = [d for d in week["days"] if d["morning"]["sick"] and d["morning"]["broken"] and not plan.opens_on(d)]
    quiet = [d for d in week["days"] if not d["morning"]["sick"] and not d["morning"]["broken"]]
    require(moot and quiet, "control: the week has no day to test the opening rule against")
    print(f"  control: the opening rule refuses {moot[0]['day']}, whose breakdown moved no call, and {quiet[0]['day']}, which had no event")

    all_jobs = backtest.test_jobs(conn)
    load.mark_unplanned(all_jobs)    # the day's events include the minute each unplanned call comes in
    days = sorted({j["day"] for j in all_jobs})
    by_day = defaultdict(list)
    for j in all_jobs:
        by_day[j["day"]].append(j)
    techs = week["ctx"].crew.techs

    def events_differ(shift):
        return [day for day in week["ours"]["events"] if
                dispatch.events(by_day[day], techs, dispatch.SEED + days.index(day) + shift)[:2] != week["ours"]["events"][day]]
    print(f"  the week's sick calls and breakdowns against a recount on the 92-day seeds: {len(events_differ(0))} days differ")
    require(not events_differ(0), "the week's surprises are not the ones the 92-day run met on the same days")
    require(events_differ(1), "control: a recount on seeds one day off still matched, so the recount cannot see a change")

    day, d = week["days"][0]["day"], week["days"][0]
    rec = week["ours"]["days"][day]
    fleet = week["ctx"].fleet
    plain = sorted(p for p in fleet.size if p not in fleet.locked and p not in rec["stuck"])
    tech = next(t for t in techs if t not in fleet.certified and rec["load"][t])
    asked = next(i for i, j in enumerate(week["jobs"]) if j["ai"]["question"])
    off_code = next(i for i, j in enumerate(week["jobs"]) if j["booked"] != j["problem"])

    def with_load(cargo):
        out = copy.copy(week)
        out["ours"] = dict(week["ours"], days=dict(week["ours"]["days"]))
        out["ours"]["days"][day] = dict(rec, load=dict(rec["load"], **{tech: cargo}))
        return out

    def with_job(i, **change):
        out = copy.copy(week)
        out["jobs"] = list(week["jobs"])
        out["jobs"][i] = dict(week["jobs"][i], **change)
        return out

    moved_day = next(x for x in week["days"] if x["morning"]["moved"])
    wrong_to = copy.copy(week)
    wrong_to["days"] = [dict(x, morning=dict(x["morning"], moved=[dict(x["morning"]["moved"][0], to="T99")]
                                             + x["morning"]["moved"][1:])) if x is moved_day else x for x in week["days"]]
    no_sick = copy.copy(week)
    no_sick["days"] = [dict(x, morning=dict(x["morning"], sick=[])) if x["day"] == week["open"] else x for x in week["days"]]
    planted = {
        "over its space": with_load(rec["load"][tech] | set(plain)),
        "a locked part on an uncertified truck": with_load(rec["load"][tech] | {min(fleet.locked)}),
        "a back-ordered part the truck did not already hold":
            with_load(rec["load"][tech] | {next(p for p in sorted(rec["stuck"]) if p not in rec["before"][tech])}),
        "a load its parts' worth does not reproduce": with_load(rec["load"][tech] - {min(rec["load"][tech])}),
        "a call not on Claude's code": with_job(off_code, problem=week["jobs"][off_code]["booked"]),
        "an unsure call without its question": with_job(asked, ai=dict(week["jobs"][asked]["ai"], question=None)),
        "a moved call without its from and to": wrong_to,
        "a sick call or breakdown missing from the morning": no_sick,
        "day totals that disagree with the replay": dict(week, ours=dict(week["ours"], served=week["ours"]["served"] + 1)),
    }
    caught = {kind: kind in plan_faults(w, followups) for kind, w in planted.items()}
    print(f"  control: {sum(caught.values())} of {len(caught)} planted plan faults caught, each by its own rule")
    require(all(caught.values()), f"the plan check missed a planted fault: {[k for k, v in caught.items() if not v]}")


def results_written(conn):
    """Fill every results table the way run.py does, unless an earlier check in this run already has."""
    from staple import backorders, backtest, dispatch, load, triage
    for table, fill in (("backtest", lambda: backtest.run(conn)),
                        ("load_policy", lambda: load.run(conn)),
                        ("backorder_list", lambda: backorders.run(conn)),
                        ("triage", lambda: triage.run(conn, *triage.load(conn))),
                        ("dispatch", lambda: dispatch.run(conn))):
        if not one(conn, f"SELECT COUNT(*) FROM {table}"):
            fill()


def to(value, places):
    """A recomputed value beside the decimal places its query shows, so the comparison knows what rounding can do."""
    return (value, places)


def hand_tables(conn, stock_as_replayed=True):
    """Every column of every metric query recomputed in Python, apart from the SQL, as {query: {row: {column: value}}}.
    The four history measures are counted again from the jobs, the parts they needed and the truck stock. The others
    report a results table, so its raw rows are read and the query's arithmetic is redone. The limit: for those
    this guards the query and not the replay that filled the table, which the engine's own checks hold. One row
    goes further: the return on today's trucks is counted again from the jobs and the stock. Pass
    stock_as_replayed=False where the stock was changed after the replay ran, and that recount is left out."""
    from datetime import date, timedelta

    def pct(share):
        return None if share is None else to(100 * share, 1)

    def of(part, whole):
        return pct(part / whole) if whole else None    # SQL gives NULL for a share of nothing

    jobs = conn.execute("SELECT job, day, type, tech, parts_on_truck, first_visit_fix FROM jobs").fetchall()
    needed = defaultdict(set)
    for job, part in conn.execute("SELECT job, part FROM job_parts"):
        needed[job].add(part)
    held = defaultdict(dict)
    for truck, part, qty in conn.execute("SELECT truck, part, qty FROM truck_stock"):
        held[truck][part] = qty
    trucks = conn.execute("SELECT truck, tech FROM trucks").fetchall()
    aboard = {tech: set(held.get(truck, ())) for truck, tech in trucks}
    cost = dict(conn.execute("SELECT part, cost FROM parts"))

    month, kind = defaultdict(list), defaultdict(Counter)
    for job, day, kind_, tech, on_truck, fixed in jobs:
        month[day[:7]].append(fixed)
        c = kind[kind_]
        c["jobs"] += 1
        c["repeats"] += not fixed
        c["parts_caused"] += not fixed and not on_truck
        if tech in aboard:    # the query reads a job's parts through its technician's truck
            c["lines"] += len(needed[job])
            c["on"] += len(needed[job] & aboard[tech])
            c["needing"] += bool(needed[job])
            c["whole"] += bool(needed[job]) and needed[job] <= aboard[tech]

    test_from = one(conn, "SELECT MIN(day) FROM jobs WHERE split = 'test'")
    year_before = (date.fromisoformat(test_from) - timedelta(days=365)).isoformat()
    used = {(tech, p) for job, day, _, tech, *_ in jobs if year_before <= day < test_from for p in needed[job]}
    slow = {}
    for truck, tech in trucks:
        value = {p: cost[p] * qty for p, qty in held.get(truck, {}).items()}
        idle = [v for p, v in value.items() if (tech, p) not in used]
        if value:
            slow[truck] = {"tech": tech, "parts_on_board": len(value), "stock_value": to(sum(value.values()), 0),
                           "parts_unused": len(idle), "unused_value": to(sum(idle), 0),
                           "unused_value_pct": of(sum(idle), sum(value.values()))}

    cover = {(method, k): c for method, k, c in conn.execute("SELECT method, k, coverage FROM backtest")}

    # Today's trucks, counted again from the held-back jobs and the stock: the jobs a truck-day that needed a part
    # and found every one aboard, and the return on the stock that covered them.
    worked = Counter((day, tech) for _, day, _, tech, *_ in jobs if day >= test_from)
    saved_today = sum(1 for job, day, _, tech, *_ in jobs
                      if day >= test_from and needed[job] and needed[job] <= aboard[tech]) / len(worked)
    stock_today = sum(sum(cost[p] for p in aboard[tech]) for _, tech in worked) / len(worked)
    repeat_visit = one(conn, "SELECT value FROM assumptions WHERE name = 'repeat_visit'")
    loads = {
        (policy, k): {"covered_pct": pct(covered), "part_jobs_covered": to(saved, 2), "space_used": to(space, 1),
                      "parts_aboard": to(parts, 1), "permanent_parts": to(permanent, 1), "moves_a_night": to(moves, 1),
                      "stock_value": to(value, 0), "repeat_cost": to(repeat, 2), "handling_cost": to(handling, 2),
                      "holding_cost": to(holding, 2), "cost_a_truck_day": to(total, 2), "roi": to(roi, 2)}
        for policy, k, covered, saved, space, parts, permanent, moves, value, repeat, handling, holding, total, roi in
        conn.execute("SELECT policy, k, coverage, saved, space, parts, permanent, moves, value, repeat_cost, handling, "
                     "holding, total, roi FROM load_policy")}
    if stock_as_replayed:
        loads[("today's trucks", 0)].update(part_jobs_covered=to(saved_today, 2),
                                            roi=to(saved_today * repeat_visit * 365 / stock_today, 2))

    falls_back = one(conn, "SELECT value FROM assumptions WHERE name = 'falls_back'")
    quoted = one(conn, "SELECT value FROM assumptions WHERE name = 'carry_list_quoted'")
    cheapest = dict(conn.execute("SELECT policy, MIN(total) FROM load_policy GROUP BY policy"))
    fixes = {policy: 100 * share for policy, share in conn.execute("SELECT policy, fixes_per_call FROM dispatch")}
    read = {method: 100 * share for method, share in conn.execute("SELECT method, coverage FROM triage")}

    def control(gain, measure, places, without, with_it, broken):
        shown = [round(v, places) for v in (without, with_it, broken)]
        share = (shown[2] - shown[0]) / (shown[1] - shown[0])
        return {"gain_tested": gain, "measure": measure, "without_it": to(without, places), "with_it": to(with_it, places),
                "with_control": to(broken, places), "gain_kept_pct": to(100 * share, 0),
                "verdict": "falls back" if share < falls_back else "does not fall back"}

    first, list_covers, dollars = "calls fixed on the first visit, %", "jobs the 3-part list covers, %", "cost a truck-day, $"
    controls = {
        "shuffled-history control": control(
            "the predictor over the national top parts", "jobs the quoted carry list covers, %", 1,
            *(100 * cover[(m, quoted)] for m in ("national top parts", "predictor", "shuffled-history control"))),
        "top-up from shuffled history": control(
            "topping today's stock up with the day's lists", dollars, 2,
            *(cheapest[p] for p in ("today's trucks", "today's stock plus the day's lists", "top-up from shuffled history"))),
        "balanced from shuffled history": control(
            "the balanced truck over today's stock", dollars, 2,
            *(cheapest[p] for p in ("today's trucks", "balanced truck", "balanced from shuffled history"))),
        "Staple with shuffled skills (control)": control(
            "knowing each technician's skill", first, 1,
            *(fixes[p] for p in ("parts only, balanced loads", "Staple: skills and parts, balanced loads",
                                 "Staple with shuffled skills (control)"))),
        "Staple with shuffled-history parts odds (control)": control(
            "the dispatcher judging each truck's parts", first, 1,
            *(fixes[p] for p in ("skills only, balanced loads", "Staple: skills and parts, balanced loads",
                                 "Staple with shuffled-history parts odds (control)"))),
        "Claude answers shuffled (control)": control(
            "Claude reading the customer's words", list_covers, 1,
            *(read[m] for m in ("no code", "Claude", "Claude answers shuffled (control)"))),
        "Claude, asking when unsure, random replies (control)": control(
            "Claude asking one question when unsure", list_covers, 1,
            *(read[m] for m in ("Claude", "Claude, asking when unsure",
                                "Claude, asking when unsure, random replies (control)"))),
    }
    return {
        "first_time_fix": {m: {"jobs": len(v), "first_time_fix_pct": of(sum(v), len(v))} for m, v in month.items()},
        "parts_caused_reschedules": {
            t: {"jobs": c["jobs"], "repeat_visits": c["repeats"], "parts_caused": c["parts_caused"],
                "parts_caused_per_100_jobs": of(c["parts_caused"], c["jobs"]),
                "parts_share_of_repeats_pct": of(c["parts_caused"], c["repeats"])} for t, c in kind.items()},
        "truck_fill_rate": {
            t: {"part_lines": c["lines"], "line_fill_pct": of(c["on"], c["lines"]), "jobs_needing_parts": c["needing"],
                "job_fill_pct": of(c["whole"], c["needing"])} for t, c in kind.items() if c["lines"]},
        "slow_truck_stock": slow,
        "carry_list_curve": {
            k: {"predictor_pct": pct(cover.get(("predictor", k))),
                "national_pct": pct(cover.get(("national top parts", k))),
                "shuffled_pct": pct(cover.get(("shuffled-history control", k))),
                "leaked_pct": pct(cover.get(("predictor that has seen the held-back jobs (leak test)", k))),
                "todays_trucks_pct": pct(cover.get(("today's trucks", 0)))} for k in {k for _, k in cover if k > 0}},
        "ai_step": {
            method: {"jobs": n, "agrees_booked_pct": pct(booked), "agrees_true_pct": pct(true), "coverage_pct": pct(covered)}
            for method, n, booked, true, covered in
            conn.execute("SELECT method, jobs, agree_booked, agree_true, coverage FROM triage")},
        "dispatch_scorecard": {
            policy: {"calls": calls, "rolled": rolled, "first_visit_fix_pct": pct(fixes), "parts_missing_pct": pct(missing),
                     "drive_minutes": to(drive, 1), "moved": moved, "moved_fix_pct": pct(moved_fix),
                     "cost_per_call": to(dollars, 2)}
            for policy, calls, rolled, fixes, missing, drive, moved, moved_fix, dollars in
            conn.execute("SELECT policy, calls, rolled, fixes_per_call, parts_missing, drive_per_call, moved, "
                         "moved_fix_rate, cost_per_call FROM dispatch")},
        "backorder_exposure": {
            (day, part): {"ships_in_days": left, "trucks_holding": len(holders.split()), "open_zones": zones,
                          "expected_calls": to(calls, 1), "expected_stuck": to(stuck, 1), "dollars_at_risk": to(risk, 0),
                          "actual_calls": came, "actual_stuck": came_stuck, "back_test": int(first and whole)}
            for day, part, left, holders, zones, calls, stuck, risk, came, came_stuck, first, whole in
            conn.execute("SELECT day, part, days_left, holders, open_zones, expected_calls, expected_stuck, at_risk, "
                         "actual_calls, actual_stuck, first_listed, whole_wait FROM backorder_list")},
        # Every row the load replay wrote, none left out. The return is held to the replay's own figure, and for
        # today's trucks to the count made above from the jobs and the stock.
        "inventory_roi": loads,
        "controls": controls,
    }


ROW_KEYS = {"inventory_roi": 2, "backorder_exposure": 2}    # how many leading columns name a row, where one does not


def rows_by_key(name, result):
    columns, rows = result
    n = ROW_KEYS.get(name, 1)
    return {(r[0] if n == 1 else tuple(r[:n])): dict(zip(columns[n:], r[n:])) for r in rows}


def query_gaps(name, result, hand):
    """Where one metric query and its Python recomputation part ways, one line each. A decimal may differ by half a
    unit of the last place its query shows, which is all rounding can do. A count or a name must be equal."""
    found = rows_by_key(name, result)
    gaps = [f"{name} returns one row twice"] if len(found) != len(result[1]) else []
    for key in sorted(set(found) | set(hand), key=str):
        if key not in found or key not in hand:
            gaps.append(f"{name}, row {key}: only in the {'recomputation' if key in hand else 'query'}")
            continue
        for column in sorted(set(found[key]) | set(hand[key])):
            if column not in found[key] or column not in hand[key]:
                gaps.append(f"{name}, row {key}, {column}: only in the {'recomputation' if column in hand[key] else 'query'}")
                continue
            got, want = found[key][column], hand[key][column]
            value, places = want if isinstance(want, tuple) else (want, None)
            if places is None or got is None or value is None or isinstance(got, str):
                same = got == value
            else:
                same = abs(got - value) <= 0.5 * 10 ** -places + 1e-9
            if not same:
                shown = round(value, 4) if isinstance(value, float) else value
                gaps.append(f"{name}, row {key}, {column}: the query gives {got}, the recomputation {shown}")
    return gaps


def metric_gaps(results, hand):
    require(set(results) == set(hand),
            f"a metric query and its Python recomputation do not pair up: {sorted(set(results) ^ set(hand))}")
    return [gap for name in sorted(results) for gap in query_gaps(name, results[name], hand[name])]


@check("sql")
def sql_metrics():
    import sqlite3
    from staple import metrics
    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
        files = sorted(Path(tmp).glob("*.csv"))
        for name, (columns, rows) in results.items():
            print(f"  {name}: {len(rows)} rows, {len(columns)} columns")
            require(rows, f"{name} returns no rows")
            require(all(v is not None for r in rows for v in r), f"{name} returns an empty value")
        require(len(files) == len(results) == len(list(metrics.SQL.glob("*.sql"))), "a metric did not write its CSV")

        hand = hand_tables(conn)
        gaps = metric_gaps(results, hand)
        values = sum(len(row) for table in hand.values() for row in table.values())
        print(f"  every column of the {len(results)} queries recomputed in Python apart from the SQL: "
              f"{values - len(gaps)} of {values} values agree")
        require(not gaps, f"the SQL and the Python recomputation disagree on {len(gaps)} values, first: " + "; ".join(gaps[:3]))

        # Control: plant changes in a copy's data and watch the history metrics move, so agreement is not two blind checks.
        watched = ("first_time_fix", "truck_fill_rate", "slow_truck_stock")
        base = {name: rows_by_key(name, results[name]) for name in watched}
        copy = sqlite3.connect(":memory:")
        conn.backup(copy)
        first = min(base["first_time_fix"])
        truck, _, part = copy.execute("""SELECT t.truck, t.tech, s.part FROM truck_stock s JOIN trucks t USING (truck)
                                            JOIN job_parts jp ON jp.part = s.part JOIN jobs j ON j.job = jp.job AND j.tech = t.tech
                                            ORDER BY t.truck, s.part LIMIT 1""").fetchone()
        other = one(copy, "SELECT MAX(truck) FROM trucks")
        copy.execute("UPDATE jobs SET first_visit_fix = 0 WHERE substr(day, 1, 7) = ?", (first,))
        copy.execute("DELETE FROM truck_stock WHERE truck = ? AND part = ?", (truck, part))
        # Every technician's jobs have needed every real part at some point, so the unused part is a new one.
        copy.execute("INSERT INTO parts VALUES ('washer/planted part', 'washer', 'planted part', 50, 1, 0)")
        copy.execute("INSERT INTO truck_stock VALUES (?, 'washer/planted part', 1)", (other,))
        after = metrics.run(copy, Path(tmp) / "planted")
        planted = {name: rows_by_key(name, after[name]) for name in watched}
        kind = one(copy, "SELECT type FROM parts WHERE part = ?", part)
        moved = {"a month's fixes zeroed": planted["first_time_fix"][first]["first_time_fix_pct"] == 0
                                           < base["first_time_fix"][first]["first_time_fix_pct"],
                 "a needed part taken off a truck": planted["truck_fill_rate"][kind]["line_fill_pct"]
                                                    < base["truck_fill_rate"][kind]["line_fill_pct"],
                 "an unused part put on a truck": planted["slow_truck_stock"][other]["unused_value"]
                                                  == base["slow_truck_stock"][other]["unused_value"] + 50}
        print(f"  control: planted changes in the data moved {sum(moved.values())} of {len(moved)} metrics")
        require(all(moved.values()), f"a metric did not move under a planted change: {moved}")
        # The copy's replay tables were filled before a part came off a truck, so the recount from that stock is left out.
        apart = metric_gaps(after, hand_tables(copy, stock_as_replayed=False))
        require(not apart, "the recomputation and the SQL part ways on planted data: " + "; ".join(apart[:3]))
        recount = hand_tables(copy)["inventory_roi"][("today's trucks", 0)]["part_jobs_covered"][0]
        replayed = one(copy, "SELECT saved FROM load_policy WHERE policy = 'today''s trucks'")
        print(f"  control: with that part off its truck, the recount of jobs today's trucks cover falls from "
              f"{replayed:.4f} a truck-day to {recount:.4f}")
        require(recount < replayed, "control: taking a needed part off a truck did not move the recount of today's trucks")
        copy.close()


# One wrong version of every metric query: the text to find, what replaces it, and what the wrong query then does.
WRONG_QUERIES = {
    "ai_step": ("100.0 * agree_true", "100.0 * agree_booked",
                "reports agreement with the booked code as agreement with the true problem"),
    "backorder_exposure": ("ROUND(at_risk, 0)", "ROUND(expected_stuck, 0)", "reports stuck calls as dollars at risk"),
    "carry_list_curve": ("WHERE k > 0", "WHERE k > 1", "drops the one-part carry list"),
    "dispatch_scorecard": ("100.0 * fixes_per_call", "100.0 * moved_fix_rate",
                           "reports the moved calls' fix rate as every call's"),
    "first_time_fix": ("AVG(first_visit_fix)", "AVG(parts_on_truck)", "counts a visit with its parts aboard as a fix"),
    "controls": ("< a.value", ">= a.value", "calls a control that kept its gain fallen back, and one that lost it kept"),
    "inventory_roi": ("l.saved * a.value", "l.coverage * l.jobs * a.value",
                      "counts a job that needed no part as a repeat visit the stock avoided"),
    "parts_caused_reschedules": ("AND parts_on_truck = 0) / COUNT(*)", "AND parts_on_truck = 1) / COUNT(*)",
                                 "counts the repeats that had their parts as caused by parts"),
    "slow_truck_stock": ("'-365 days'", "'-30 days'", "looks back one month for use, not twelve"),
    "truck_fill_rate": ("MIN(on_truck)", "MAX(on_truck)", "passes a job when any one of its parts was aboard"),
}


@check("mutants")
def mutants():
    """Every metric query against one wrong version of itself. The recomputation that agrees with the real query
    must refuse the wrong one, or it is not guarding that query."""
    from staple import metrics
    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
    hand = hand_tables(conn)
    gaps = metric_gaps(results, hand)
    print(f"  the {len(results)} metric queries as written against their Python recomputation: {len(gaps)} values apart")
    require(not gaps, "a query as written already disagrees with its recomputation, so a wrong version proves nothing: "
                      + "; ".join(gaps[:3]))
    require(set(WRONG_QUERIES) == set(results),
            f"a metric query has no wrong version planted for it: {sorted(set(results) ^ set(WRONG_QUERIES))}")
    passed = []
    for name, (right, wrong, does) in WRONG_QUERIES.items():
        text = (metrics.SQL / f"{name}.sql").read_text(encoding="utf-8")
        require(text.count(right) == 1, f"the wrong version of {name} no longer finds `{right}` once, so it plants nothing")
        cur = conn.execute(text.replace(right, wrong))
        refused = query_gaps(name, ([d[0] for d in cur.description], cur.fetchall()), hand[name])
        print(f"  control: {name}, changed so it {does}: {len(refused)} values refused")
        if not refused:
            passed.append(name)
    require(not passed, f"a wrong version of a metric query passed its recomputation: {', '.join(passed)}")


WHOLE_TABLES = {"inventory_roi": ("load_policy", "policy, k"), "dispatch_scorecard": ("dispatch", "policy"),
                "ai_step": ("triage", "method"), "backorder_exposure": ("backorder_list", "day, part")}
CONTROL_WORDS = ("control", "shuffled", "random")    # a results row whose name holds one of these is a control


def hidden_rows(conn, results):
    """Rows a results table holds that the query reporting it leaves out. Each of these queries reports its table
    whole, so a filter in one hides a result."""
    out = []
    for name, (table, key) in WHOLE_TABLES.items():
        width = len(key.split(", "))
        shown = {tuple(r[:width]) for r in results[name][1]}
        out += [f"{name} leaves out {' / '.join(map(str, row))}" for row in conn.execute(f"SELECT {key} FROM {table}")
                if tuple(row) not in shown]
    return out


def unlisted_controls(conn, results):
    """Rows the engine names a control that the controls query does not list."""
    listed = {r[0] for r in results["controls"][1]}
    named = set()
    for table, column in (("backtest", "method"), ("load_policy", "policy"), ("dispatch", "policy"), ("triage", "method")):
        named |= {name for (name,) in conn.execute(f"SELECT DISTINCT {column} FROM {table}")
                  if any(word in name for word in CONTROL_WORDS)}
    return sorted(named - listed)


def card_text(page, name):
    """One evidence card of the report as a reader meets it: its words and its hover hints."""
    import html
    start = page.index(f'id="proof-{name}"')
    return html.unescape(page[start:page.index("</section>", start)])


def figures_missing(page, results):
    """Each control or test whose own figures the report's cards do not carry, read from the queries' rows."""
    from staple import load, report

    def named(query, key):
        return {tuple(r[:key]) if key > 1 else r[0]: dict(zip(results[query][0], r)) for r in results[query][1]}

    loads, curve, controls = named("inventory_roi", 2), named("carry_list_curve", 1), named("controls", 1)
    scores = named("dispatch_scorecard", 1)
    cost, covers = (lambda r: f"${r['cost_a_truck_day']:.2f}"), (lambda r: report.pct(r["covered_pct"]))
    shuffled, equal = loads[(load.CONTROL_BALANCED, 0)], loads[(load.EQUAL_SPACE, 0)]
    odds = "Staple with shuffled-history parts odds (control)"
    want = {
        "the balanced truck loaded from shuffled history": ("inventory_roi", [covers(shuffled), cost(shuffled),
                                                                              report.kept(controls[load.CONTROL_BALANCED])]),
        "the balanced truck held to today's space": ("inventory_roi", [covers(equal), cost(equal)]),
        "the balanced truck at each higher move price": ("inventory_roi", [cost(loads[(load.at_move(m), 0)]) for m in load.MOVE_STEPS]),
        "the leak test": ("carry_list_curve", [report.pct(curve[load.SHOWN_K]["leaked_pct"])]),
        "the parts-odds control": ("dispatch_scorecard", [report.pct(scores[odds]["first_visit_fix_pct"]),
                                                          report.kept(controls[odds])]),
    }
    want.update({f"the control '{name}'": ("controls", [name, row["verdict"], f"{row['without_it']} without it",
                                                        f"{row['with_it']} with it", f"{row['with_control']} with the control"])
                 for name, row in controls.items()})
    return sorted(what for what, (card, texts) in want.items() if any(t not in card_text(page, card) for t in texts))


@check("controls-shown")
def controls_shown():
    """Every control and test the engine runs reaches the report with its real result, and no query hides a row."""
    import sqlite3
    from staple import load, metrics, report
    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))

    # No query hides a row, and every control has a row of its own.
    hidden, unlisted = hidden_rows(conn, results), unlisted_controls(conn, results)
    held = sum(len(conn.execute(f"SELECT 1 FROM {table}").fetchall()) for table, _ in WHOLE_TABLES.values())
    print(f"  {held:,} rows in the {len(WHOLE_TABLES)} results tables a query reports whole: {len(hidden)} left out; "
          f"{len(results['controls'][1])} controls, {len(unlisted)} without a row in the controls query")
    require(not hidden, "a query hides rows of its results table: " + "; ".join(hidden[:3]))
    require(not unlisted, f"a control has no row in the controls query: {unlisted}")
    text = (metrics.SQL / "inventory_roi.sql").read_text(encoding="utf-8")
    cur = conn.execute(text.replace("ORDER BY", "WHERE l.policy NOT LIKE '%shuffled%' ORDER BY"))
    filtered = dict(results, inventory_roi=([d[0] for d in cur.description], cur.fetchall()))
    copy = sqlite3.connect(":memory:")
    conn.backup(copy)
    copy.execute("INSERT INTO dispatch SELECT 'Staple with shuffled zones (control)', calls, served, rolled, fix_rate, "
                 "fixes_per_call, parts_missing, drive_per_call, moved, moved_fix_rate, cost_per_call FROM dispatch LIMIT 1")
    planted = unlisted_controls(copy, results)
    copy.close()
    print(f"  control: a query that filters out the shuffled rows hides {len(hidden_rows(conn, filtered))} rows, and a "
          f"control planted in the dispatch table is named: {planted}")
    require(len(hidden_rows(conn, filtered)) > 1, "control: a query filtering out the shuffled rows was not caught")
    require(planted == ["Staple with shuffled zones (control)"], "control: a planted control with no row was not caught")

    # The return on stock, and the order of its rows.
    columns, rows = results["inventory_roi"]
    loads = {(r[0], r[1]): dict(zip(columns, r)) for r in rows}
    jobs = load.test_jobs(conn)
    none = sum(1 for j in jobs if not j["needed"])
    bal = loads[(load.BALANCED, 0)]
    raw = conn.execute("SELECT coverage * jobs, saved FROM load_policy WHERE policy = ?", (load.BALANCED,)).fetchone()
    print(f"  {none:,} of {len(jobs):,} held-back jobs needed no part; counting them would lift the balanced truck's "
          f"return from {bal['roi']:.2f} to {bal['roi'] * raw[0] / raw[1]:.2f}")
    require(none and raw[0] > raw[1], "control: no held-back job needs no part, so the return cannot tell the two counts apart")
    costs = [r[columns.index("cost_a_truck_day")] for r in rows]
    by_return = sorted(rows, key=lambda r: -r[columns.index("roi")])
    print(f"  the cheapest truck-day leads the table: {rows[0][0]} at ${costs[0]:.2f}; ordered by return, "
          f"{by_return[0][0]} would lead at ${by_return[0][columns.index('cost_a_truck_day')]:.2f}")
    require(costs == sorted(costs), "the inventory query does not list its rows from the cheapest truck-day up")
    require(by_return[0][columns.index("cost_a_truck_day")] > costs[0],
            "control: ordering by return also puts the cheapest first, so the order test proves nothing")

    # The balanced truck's tests, replayed here and held to what they claim.
    fleet = load.Fleet(conn)
    load.mark_unplanned(jobs)
    lists, surprise = load.ranked_lists(load.Predictor(conn), jobs), load.surprise_value(conn, load.Predictor(conn))
    tight, roomy, frozen = load.replay(fleet, jobs, [
        ("held to today's space", 0, load.balanced(fleet, lists, surprise, equal_space=True)),
        ("the whole truck", 0, load.balanced(fleet, lists, surprise))], record=True) + load.replay(
        fleet, jobs, [("a prohibitive move", 0, load.balanced(fleet, lists, surprise, move=1000.0))], move=1000.0)

    def over(row):
        return sum(sum(fleet.size[p] for p in parts) > fleet.used(tech) for (_, tech), parts in row["loads"].items())

    print(f"  held to the space today's stock uses, {over(tight)} of {len(tight['loads']):,} loads take more than that "
          f"space; given the whole truck, {over(roomy):,} do")
    require(over(tight) == 0, "the equal-space truck takes more space than today's stock on some truck-day")
    require(over(roomy) > 0, "control: the whole-truck load never passes today's space, so the space test cannot fail")
    today = loads[(load.TODAY, 0)]
    print(f"  at $1,000 a move the balanced truck moves {frozen['moves']:.2f} parts a night and costs "
          f"${frozen['total']:.2f}, against today's ${today['cost_a_truck_day']:.2f}; at ${load.MOVE:.0f} it moves "
          f"{bal['moves_a_night']}")
    require(frozen["moves"] == 0 and abs(frozen["total"] - today["cost_a_truck_day"]) < 0.005,
            "at a prohibitive price for a move the balanced truck does not become today's truck")
    require(bal["moves_a_night"] > 0, "control: the balanced truck moves nothing at the usual price either")
    steps = [loads[(load.at_move(m), 0)]["moves_a_night"] for m in load.MOVE_STEPS]
    require(all(a >= b for a, b in zip([bal["moves_a_night"]] + steps, steps)), "a higher price for a move does not mean fewer moves")

    # The page: each control and test with its own figures, and words that match the verdicts.
    week, _ = shared_week()
    stamp_text, day, _, _ = report.stamp()
    page = report.build_report(results, week, stamp_text, day)
    missing = figures_missing(page, results)
    curve = {r[0]: dict(zip(results["carry_list_curve"][0], r)) for r in results["carry_list_curve"][1]}[load.SHOWN_K]
    gap = curve["leaked_pct"] - curve["predictor_pct"]
    verdicts = Counter(r[results["controls"][0].index("verdict")] for r in results["controls"][1])
    print(f"  the report carries the figures of {5 + len(results['controls'][1]) - len(missing)} of "
          f"{5 + len(results['controls'][1])} controls and tests; verdicts: {dict(verdicts)}; the leak test moves the "
          f"predictor {gap:+.1f} points")
    require(not missing, f"the report does not carry the figures of: {missing}")
    require(abs(gap) <= 1.0, "the report calls the leak gap small, and it is now over a point: rewrite the card")

    def changed(query, key, column, value):
        columns, rows = results[query]
        at = columns.index(column)
        return dict(results, **{query: (columns, [r[:at] + (value(r[at]),) + r[at + 1:] if r[0] == key else r for r in rows])})

    off_by_one = changed("inventory_roi", load.EQUAL_SPACE, "cost_a_truck_day", lambda v: v + 1)
    wrong_page = report.build_report(off_by_one, week, stamp_text, day)
    require(figures_missing(wrong_page, results) == ["the balanced truck held to today's space"],
            "control: a report whose equal-space cost is a dollar off was not caught, or the wrong card was blamed")
    odds = "Staple with shuffled-history parts odds (control)"
    turned = changed("controls", odds, "verdict", lambda v: "falls back")
    try:
        report.build_report(turned, week, stamp_text, day)
        stopped = False
    except ValueError:
        stopped = True
    print(f"  control: a report with the equal-space cost a dollar off is refused, and a build whose query says the "
          f"parts-odds control falls back {'stops' if stopped else 'goes on'}")
    require(stopped, "control: the report was built though its words contradict the controls query")


class Hints(HTMLParser):
    """Every element a reader would hover that carries no hint. A dotted value may borrow its cell's hint;
    everything else must carry its own, because the hover layer shows the innermost hint it finds."""
    VOID = {"meta", "br", "img", "input", "hr", "link", "wbr"}
    CLASSES = {"tile", "bar-row", "key", "madeup", "hv", "q", "btn", "build-stamp", "content-day", "fig-title",
               "blk", "stop", "who", "ask", "todo-item", "evt", "day-btn", "side-btn", "vbtn", "lane-note", "brk",
               "invented", "pick-label", "legend-line"}

    def __init__(self, tags):
        super().__init__()
        self.tags, self.stack, self.missing = tags, [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        own = "data-tip" in a or (tag == "svg" and "data-curve" in a)
        classes = set((a.get("class") or "").split())
        needs = tag in self.tags or bool(classes & self.CLASSES)
        covered = own or ("hv" in classes and any(t for _, t in self.stack))
        if needs and not covered:
            self.missing.append(f"<{tag} class=\"{a.get('class') or ''}\"> at line {self.getpos()[0]}")
        if tag not in self.VOID:
            self.stack.append((tag, own))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def missing_hints(page, kind):
    parser = Hints({"h1", "summary", "svg"} | ({"h2", "th"} if kind == "report" else set()))
    parser.feed(page)
    return parser.missing


def page_faults(page, pattern, kind="report"):
    """What a page must carry: the build stamp in its own pattern (or, where git holds no history, the
    words that say so), the day its results last changed, a hint on everything a reader would hover,
    every placeholder filled, nothing fetched from the network, and on the report a made-up label and a
    table on every chart."""
    from staple import report
    faults = []
    found = re.search(r'<span class="build-stamp"[^>]*>([^<]*)</span>', page)
    shown = html_text(found.group(1)) if found else None
    if not shown or not (re.fullmatch(pattern, shown) if pattern else shown == report.NO_HISTORY):
        faults.append("no build stamp")
    day = re.search(r'<span class="content-day"[^>]*>([^<]*)</span>', page)
    if not day or not (re.fullmatch(r"[1-9]\d? [A-Z][a-z]{2} \d{4}", day.group(1))
                       or (not pattern and day.group(1) == "unknown")):
        faults.append("no day the results last changed")
    if kind == "report":
        figures = page.count("<figure>")
        if not figures or page.count('class="madeup"') != figures or page.count("<table>") != figures:
            faults.append("a chart without its made-up label or its table")
    missing = missing_hints(page, kind)
    if missing:
        faults.append(f"{len(missing)} elements without a hint, first {missing[0]}")
    if "{{" in page:
        faults.append("a placeholder left unfilled")
    if re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//|@import""", page):
        faults.append("something fetched from the network")
    style = re.search(r"<style>(.*?)</style>", page, re.S)
    faults += selected_faults(style.group(1)) if style else ["no styles"]
    if not re.search(r'<html[^>]*\sdata-theme="light"', page):
        faults.append("a page that does not open in light")
    if not re.search(r"<button[^>]*\sdata-theme-toggle", page):
        faults.append("no light/dark switch")
    if not all(f'href="{n}"' in page for n in ("icon.svg", "icon-32.png", "apple-touch-icon.png")):
        faults.append("a tab icon missing")
    if not re.search(r'<meta property="og:image" content="https://[^"]+/card\.png\?v=\d+">', page):
        faults.append("no share card")
    return faults


def asset_faults(folder):
    """The files beside the pages: each tab icon as drawn now, and the share card photographed from today's card."""
    from staple import report
    faults = [f"{n} differs from a fresh drawing" for n, data in report.assets().items()
              if not (Path(folder) / n).exists() or (Path(folder) / n).read_bytes() != data]
    card = (Path(folder) / report.CARD).read_bytes() if (Path(folder) / report.CARD).exists() else b""
    if card[12:16] != b"IHDR" or struct.unpack(">II", card[16:24]) != report.CARD_SIZE:
        faults.append(f"the share card is not a {report.CARD_SIZE[0]} by {report.CARD_SIZE[1]} PNG")
    if (report.PAGES / "card.sha").read_text(encoding="utf-8").strip() != report.card_fingerprint():
        faults.append("the share card was photographed from other words or colors; run `python run.py --card`")
    return faults


def schemes(css):
    """The color tokens of each scheme the styles define, named light, dark, or by the selector that set them."""
    found = {}
    for selector, body in re.findall(r"(:root[^{]*)\{([^{}]*)\}", css):
        selector = selector.strip()
        name = "light" if selector == ":root" else "dark" if selector == ':root[data-theme="dark"]' else selector
        found[name] = {**found.get("light", {}), **dict(re.findall(r"(--[\w-]+):\s*([^;}]+)", body))}
    return found


def contrast(a, b):
    def lum(hexcode):
        r, g, b_ = (int(hexcode[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def selected_faults(css):
    """Selected states that would not stand out. In every scheme a selected fill or edge must reach 3:1 against the
    page and the panels, the accessibility floor for a control's state, and selected text 4.5:1 against its fill."""
    themes = schemes(css)
    if set(themes) != {"light", "dark"}:
        return [f"color schemes found: {sorted(themes)}, not just light and dark"]
    faults = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r"\.(?:on|sel)\b", selector):
            continue
        fill = re.search(r"background(?:-color)?:\s*var\((--[\w-]+)\)", body)
        marks = re.findall(r"(?:background(?:-color)?|border[\w-]*|outline|box-shadow):[^;}]*?var\((--[\w-]+)\)", body)
        if not fill and not marks:
            continue
        ink = re.search(r"(?<![-\w])color:\s*var\((--[\w-]+)\)", body)
        where = selector.strip().split(",")[0]
        for name, t in themes.items():
            colors = [t.get(m, "").strip() for m in marks + ([ink[1]] if ink else [])] + [t["--page"], t["--surface"]]
            if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in colors):
                faults.append(f"{where} in {name}: a color that is not a plain hex code, so it cannot be measured")
                continue
            best = max(min(contrast(t[m], t["--page"]), contrast(t[m], t["--surface"])) for m in marks)
            if best < 3:
                faults.append(f"{where} in {name}: selected stands out only {best:.2f}:1 from the page and panels")
            if fill and ink and contrast(t[ink[1]], t[fill[1]]) < 4.5:
                faults.append(f"{where} in {name}: selected text {contrast(t[ink[1]], t[fill[1]]):.2f}:1 on its fill")
    return faults


def opening_faults(page):
    """What the day the page opens on must show of its morning: a sick technician's hatched row, a broken-down
    truck's marker, and calls tagged with the technician they moved from."""
    shown = re.findall(r'<section class="day" data-day="[^"]+">(.*?)(?=<section class="day"|<div class="pane proof-pane")', page, re.S)
    if len(shown) != 1:
        return [f"{len(shown)} days shown at once"]
    day = shown[0]
    return [what for what, mark in (("no sick row", 'class="row sick"'), ("no broken-down truck", 'class="row broken"'),
                                    ("no moved call", ">from T")) if mark not in day]


def html_text(s):
    import html
    return html.unescape(s)


def without_stamp(page):
    """The page with its stamp and results-day spans emptied, hints included, so a copy built at another
    commit compares equal when, and only when, everything else on it is the same. Only those two spans are
    touched: the same words in a sentence elsewhere on the page stay as they are."""
    for cls in ("build-stamp", "content-day"):
        page = re.sub(rf'<span class="{cls}"[^>]*>[^<]*</span>', f'<span class="{cls}"></span>', page)
    return page


def stale_pages(fresh, folder):
    from staple import report
    out = []
    for kind, name in report.FILES.items():
        path = Path(folder) / name
        if not path.exists() or without_stamp(path.read_text(encoding="utf-8")) != without_stamp(fresh[kind]):
            out.append(name)
    return out


def scratch_history(folder):
    """A small repository built with git's plumbing, which runs no hooks: commit(path, when) saves one file
    as one change dated when, and returns the change's code."""
    env = dict(clean_git_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    head = []

    def git(*args, when=None):
        dated = dict(env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when) if when else env
        r = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, env=dated)
        require(r.returncode == 0, f"git {args[0]} failed in the scratch repository: {r.stderr.strip()[:200]}")
        return r.stdout.strip()

    def commit(path, when):
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(when, encoding="utf-8")
        git("add", path)
        made = git("commit-tree", git("write-tree"), "-m", path, *(["-p", head[-1]] if head else []), when=when)
        git("update-ref", "HEAD", made)
        head.append(made)
        return made

    git("init", "-q")
    return commit


@check("stamp")
def stamp_check():
    from staple import report
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo, bare = Path(tmp) / "repo", Path(tmp) / "bare"
        repo.mkdir()
        bare.mkdir()
        commit = scratch_history(repo)
        commit("run.py", "2026-09-01T12:00:00-04:00")
        code = commit("staple/x.py", "2026-10-02T23:30:00-04:00")    # already 3 Oct in UTC: the day is the change's own
        expected = f"Build 2 · 2 Oct 2026 · {code[:7]}"
        text, day, pattern, warned = report.stamp(repo)
        print(f"  a scratch history of two changes is stamped \"{text}\", results last changed {day}")
        require(text == expected and day == "2 Oct 2026" and not warned, f"expected \"{expected}\" and 2 Oct 2026 with no warning")
        require(pattern == report.STAMP_RE and re.fullmatch(report.STAMP_RE, text), "the stamp does not fit its own pattern")

        commit("docs/index.html", "2026-10-05T09:00:00-04:00")
        require(report.stamp(repo)[0] == expected, "committing only the published copy moved the stamp")
        print("  committing only the published copy leaves the stamp as it was")
        third = commit("check.py", "2026-10-06T09:00:00-04:00")
        moved = report.stamp(repo)
        require(moved[0] == f"Build 4 · 6 Oct 2026 · {third[:7]}" and moved[1] == "2 Oct 2026",
                f"control: a change outside the published copy should move the stamp and leave the results day, got {moved[:2]}")
        print(f"  control: a change outside the published copy moves it to \"{moved[0]}\"; the results day stays {moved[1]}")

        (repo / "docs" / "new.html").write_text("x", encoding="utf-8")
        require(not report.stamp(repo)[0].endswith("+"), "an unsaved file in the published copy alone added a +")
        (repo / "staple" / "y.py").write_text("x", encoding="utf-8")
        unsaved = report.stamp(repo)
        today = report.pretty_day(datetime.date.today().isoformat())
        require(unsaved[0] == moved[0] + "+" and unsaved[1] == today, f"unsaved code should add a + and date the results today, got {unsaved[:2]}")
        print(f"  unsaved code adds a +: \"{unsaved[0]}\"; an unsaved file in the published copy alone does not")

        (repo / ".git" / "shallow").write_text(third + "\n", encoding="utf-8")
        cut = report.stamp(repo)
        require(cut[0] == f"6 Oct 2026 · {third[:7]}+" and cut[3] and re.fullmatch(report.STAMP_RE, cut[0]),
                f"a copy holding part of its history should carry no build number and say why, got {cut[0]!r}")
        print(f"  a copy holding part of its history is stamped \"{cut[0]}\" and warns")

        none = report.stamp(bare)
        require(none[0] == report.NO_HISTORY and none[2] is None and none[3], f"a folder with no history should say so, got {none[0]!r}")
        require(not re.fullmatch(report.STAMP_RE, report.NO_HISTORY), "control: the no-history words fit the stamp pattern")
        print(f"  a folder with no history says \"{none[0]}\" and warns; those words do not fit the stamp pattern")

    here = report.stamp()
    print(f"  this repository: \"{here[0]}\", results last changed {here[1]}" + "".join(f"; warning: {w}" for w in here[3]))
    require(here[0] == report.NO_HISTORY or re.fullmatch(report.STAMP_RE, here[0]), "this repository's stamp fits no known shape")

    one, _ = report.build_manuals("Build 1 · 1 Sep 2026 · abcdef1", "1 Sep 2026")
    two, _ = report.build_manuals("Build 9 · 9 Oct 2026 · 1234567+", "9 Oct 2026")
    require(one != two and without_stamp(one) == without_stamp(two),
            "two builds that differ only in their stamps do not compare equal once the stamps are emptied")
    said = one.replace("</main>", f"<p>{report.NO_HISTORY}</p><p>Build 1 · 1 Sep 2026 · abcdef1</p></main>", 1)
    require(without_stamp(said) != without_stamp(one), "control: a stamp's words in a sentence elsewhere were emptied too")
    print("  two builds differing only in their stamps compare equal once the stamps are emptied; "
          "control: the same words in a sentence elsewhere are left alone, so that page still differs")


@check("report")
def report_check():
    from staple import metrics, report
    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
        text, day, pattern, warnings = report.stamp()
        for w in warnings:
            print(f"  stamp warning: {w}")
        if not pattern:
            print(f"  build stamp: git holds no history here, so the pages must say '{report.NO_HISTORY}'")
        week, _ = shared_week()
        pages = report.build(results, week, text, day)
        for kind, page in pages.items():
            print(f"  {report.FILES[kind]}: {len(page):,} characters, {page.count('data-tip=')} hints, "
                  f"{page.count('<figure>')} charts, stamp \"{text}\", results last changed {day}")
            faults = page_faults(page, pattern, kind)
            require(not faults, f"{report.FILES[kind]} is missing: " + "; ".join(faults))
        page = pages["report"]
        ours = next(r for r in results["dispatch_scorecard"][1] if r[0].startswith("Staple:"))
        figure = f"{ours[3]:.1f}%"
        require(figure in page, "the page does not show the dispatch scorecard's own first-visit figure")
        shows = opening_faults(page)
        print(f"  the page opens on {week['open']}: {'; '.join(shows) or 'a sick row, a broken-down truck and moved calls on its board'}")
        require(not shows, f"the opening day does not show its morning: {shows}")
        quiet = next(d["day"] for d in week["days"] if not d["morning"]["sick"] and not d["morning"]["broken"])
        swapped = page.replace(f'<section class="day" data-day="{week["open"]}">', f'<section class="day" data-day="{week["open"]}" hidden>', 1)
        swapped = swapped.replace(f'<section class="day" data-day="{quiet}" hidden>', f'<section class="day" data-day="{quiet}">', 1)
        require(opening_faults(swapped), "control: a page opening on a day with no sick call or breakdown passed")
        print(f"  control: a page opening on {quiet}, which had no sick call or breakdown, is refused")

        stale = stale_pages(pages, report.PUBLISHED)
        print(f"  published copy in docs/: {len(report.FILES) - len(stale)} of {len(report.FILES)} pages match a fresh build")
        require(not stale, f"docs/ is stale or missing ({', '.join(stale)}); run `python run.py --pages` and commit docs/")
        beside = asset_faults(report.PUBLISHED)
        print(f"  beside the pages: {len(report.assets())} icon and card files; faults: {beside or 'none'}")
        require(not beside, "; ".join(beside))
        bent = Path(tmp) / "bent"
        bent.mkdir()
        for name, data in report.assets().items():
            (bent / name).write_bytes(data[:-1] + bytes([data[-1] ^ 1]) if name == "icon-32.png" else data)
        require(asset_faults(bent) == ["icon-32.png differs from a fresh drawing"], "control: a changed tab icon passed")
        print("  control: a tab icon one byte off a fresh drawing is refused")
        lede, report.LEDE = report.LEDE, report.LEDE + " Changed."
        try:
            reworded = asset_faults(report.PUBLISHED)
        finally:
            report.LEDE = lede
        require(any("photographed from other words" in f for f in reworded), "control: a card older than its words passed")
        print("  control: a share card photographed before its words changed is refused")

        tip = r'data-tip="[^"]*" tabindex="0"'
        planted = {"stamp taken out": re.sub(r'(<span class="build-stamp"[^>]*>)[^<]*', r"\1nothing", page, count=1),
                   "a made-up label taken out": re.sub(r'<span class="madeup"[^>]*>made-up data</span>', "", page, count=1),
                   "a script fetched from the network": page.replace("<script>", '<script src="https://example.com/x.js"></script><script>', 1),
                   "a column heading's hint taken off": re.sub(rf"<th {tip}>", "<th>", page, count=1),
                   "a dotted value's hint taken off": re.sub(rf'<span class="hv" {tip}>', '<span class="hv">', page, count=1),
                   "a placeholder left in": page.replace("</main>", "{{seed}}</main>", 1),
                   "a selected button the color of the page in dark mode": page.replace(
                       ".vbtn.on{background:var(--s1);color:var(--on-hi);border-color:var(--s1)",
                       ".vbtn.on{background:var(--band);color:var(--band-ink);border-color:var(--band)", 1),
                   "a page that opens dark": page.replace('<html lang="en" data-theme="light">', '<html lang="en" data-theme="dark">', 1),
                   "the light/dark switch taken out": re.sub(r"<button[^>]*data-theme-toggle.*?</button>", "", page, count=1, flags=re.S),
                   "the tab icon taken out": page.replace('<link rel="icon" href="icon.svg"', '<link rel="x" href="none.svg"', 1),
                   "the share card taken out": re.sub(r'<meta property="og:image" [^>]*>', "", page, count=1)}
        require(all(p != page for p in planted.values()), "control: a planted fault left the page unchanged")
        caught = {name: bool(page_faults(p, pattern)) for name, p in planted.items()}
        print(f"  control: {sum(caught.values())} of {len(caught)} planted faults refused")
        require(all(caught.values()), f"the page check passed a planted fault: {caught}")
        drifted = Path(tmp) / "drifted"
        drifted.mkdir()
        for kind, name in report.FILES.items():
            changed = pages[kind].replace(figure, "0.0%") if kind == "report" else pages[kind]
            (drifted / name).write_text(changed, encoding="utf-8")
        require(stale_pages(pages, drifted) == ["index.html"], "control: a published copy with one figure changed passed as current")
        print("  control: a published copy with one figure changed is refused as stale")


@check("data")
def data():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        a = built_world(Path(tmp) / "a.db")
        b = built_world(Path(tmp) / "b.db")
        same = digest(a) == digest(b)
        b.close()
        print(f"  two builds from one seed match: {same}")
        require(same, "the same seed wrote different rows")

        jobs = one(a, "SELECT COUNT(*) FROM jobs")
        test = one(a, "SELECT COUNT(*) FROM jobs WHERE split = 'test'")
        mis = one(a, "SELECT AVG(noise = 'misdescribed') FROM jobs")
        rnd = one(a, "SELECT AVG(noise = 'random') FROM jobs")
        print(f"  jobs {jobs:,}; held back for testing {test:,} ({test / jobs:.1%})")
        print(f"  planted noise: misdescribed {mis:.1%}, patternless {rnd:.1%}")
        from staple import world
        require(jobs == world.JOBS, f"expected {world.JOBS:,} jobs, found {jobs:,}")
        require(0.07 < test / jobs < 0.10, "the test split is not the last three months' share")
        require(0.07 < mis < 0.11 and 0.065 < rnd < 0.095, "planted noise is off its intended rate")

        wrong_type = one(a, """SELECT COUNT(*) FROM job_parts jp JOIN jobs j USING (job)
                               JOIN parts p USING (part) WHERE p.type != j.type""")
        require(wrong_type == 0, f"{wrong_type} job parts belong to another appliance type")

        locked_bad = one(a, """SELECT COUNT(*) FROM truck_stock s JOIN parts p USING (part)
                               JOIN trucks t USING (truck) JOIN techs h USING (tech)
                               WHERE p.locked = 1 AND h.certified = 0""")
        require(locked_bad == 0, f"{locked_bad} locked parts sit on uncertified trucks")

        short = a.execute("""SELECT t.truck, 1.0 * COALESCE(SUM(p.size), 0) / t.capacity,
                                    (SELECT COUNT(*) FROM tech_skills k JOIN parts q ON q.type = k.type
                                     JOIN techs h ON h.tech = k.tech
                                     WHERE k.tech = t.tech AND (q.locked = 0 OR h.certified = 1)
                                       AND q.part NOT IN (SELECT part FROM truck_stock WHERE truck = t.truck))
                             FROM trucks t LEFT JOIN truck_stock s USING (truck) LEFT JOIN parts p USING (part)
                             GROUP BY t.truck""").fetchall()
        part_empty = [truck for truck, used, missing in short if used < 0.85 and missing > 0]
        print(f"  today's trucks: least full {min(u for _, u, _ in short):.0%}; "
              f"{sum(1 for _, u, m in short if u < 0.85 and m == 0)} below 85% already hold every part they may carry")
        require(not part_empty, f"{len(part_empty)} of today's trucks run part-empty with parts still to carry, "
                                "which flatters every policy that fills them")

        early = one(a, "SELECT COUNT(*) FROM jobs j JOIN families f USING (family) WHERE j.day < f.first_day")
        require(early == 0, f"{early} jobs use a model family before it existed")
        thin = one(a, """SELECT MAX(n) FROM (SELECT COUNT(*) n FROM jobs j JOIN families f USING (family)
                         WHERE j.split = 'train' AND f.first_day > '2026-01-01' GROUP BY family)""")
        print(f"  newest families: at most {thin} training jobs each (thin history on purpose)")
        require(thin < 150, "the newest families are not thin enough to test the fallback")

        winter = one(a, "SELECT AVG(type = 'furnace') FROM jobs WHERE substr(day, 6, 2) IN ('12','01','02')")
        summer = one(a, "SELECT AVG(type = 'furnace') FROM jobs WHERE substr(day, 6, 2) IN ('06','07','08')")
        print(f"  furnace share of calls: winter {winter:.1%}, summer {summer:.1%}")
        require(winter > 3 * summer, "furnace calls do not follow the seasons")

        ftf = one(a, "SELECT AVG(first_visit_fix) FROM jobs")
        parts_caused = one(a, "SELECT AVG(parts_on_truck = 0) FROM jobs")
        print(f"  today's made-up first-visit fix {ftf:.1%}; parts missing on {parts_caused:.1%} of visits")
        require(0.4 < ftf < 0.85, "today's first-visit fix rate is implausible")
        a.close()


# ---- one command for everything ---------------------------------------------------------------

SEVERAL = "two|three|four|five|six|seven|eight|nine|ten"    # "one points to" is a verb, so one is left to its own forms
SPELLED_SHARE = re.compile(rf"\b(?:half|twice|thrice|doubl\w*|tripl\w*|one percent|(?:{SEVERAL})[ -](?:times|fold|percent|points)"
                           rf"|(?:one|{SEVERAL}) in (?:{SEVERAL}))\b", re.IGNORECASE)


def typed_figures(text, supplied):
    """Every figure in a document's prose that the code did not supply. A number may stand only inside a phrase
    the code itself writes, such as the job count, or as the version in a Python release's name. Code spans,
    command blocks and link targets are left out, because they hold commands and file names. The limit: a count
    spelled as a word passes, unless it is a share or a multiple, such as half or three times."""
    prose = " ".join(re.sub(r"```.*?```|`[^`]*`|\]\([^)]*\)", " ", text, flags=re.S).split())
    for phrase in supplied:
        prose = prose.replace(" ".join(phrase.split()), " ")
    prose = re.sub(r"\bPython \d+\.\d+(?:(?:,| and| or| to) \d+\.\d+)*", " ", prose)
    return re.findall(r"\$?\d[\d,.:]*\d%?|\$?\d%?", prose) + [m.group(0) for m in SPELLED_SHARE.finditer(prose)]


def doc_faults(readme, walkthrough, tracked, supplied):
    """What the README and walkthrough must hold: no figure the code did not supply, since results live on the
    generated pages and a figure typed here goes stale; every published file named in the walkthrough; and
    every relative link pointing at a file that exists."""
    faults = []
    figures = typed_figures(readme, supplied) + typed_figures(walkthrough, supplied)
    if figures:
        faults.append(f"{len(figures)} figures typed into the docs that the code did not supply: {', '.join(figures[:5])}")
    unnamed = [p for p in tracked if f"`{p}`" not in walkthrough]
    if unnamed:
        faults.append(f"{len(unnamed)} published files the walkthrough never names, first {unnamed[0]}")
    for doc, text in (("README.md", readme), ("WALKTHROUGH.md", walkthrough)):
        for link in re.findall(r"\]\(([^)#]+)\)", text):
            if not link.startswith("http") and not (ROOT / link).exists():
                faults.append(f"{doc} links {link}, which does not exist")
    return faults


@check("docs")
def docs():
    from staple import plan, triage, world
    from staple.report import PAGES_URL, written_in
    readme, walkthrough = ((ROOT / n).read_text(encoding="utf-8") for n in ("README.md", "WALKTHROUGH.md"))
    tracked = sorted(p.relative_to(ROOT).as_posix() for p in public_files())
    pins = {"the live report": PAGES_URL, "the job count": f"{world.JOBS:,} jobs", "the held-back days":
            f"last {world.TEST_DAYS} days", "the seed": f"Seed {world.SEED}", "the model": triage.MODEL,
            "the model's name": triage.MODEL_NAME, "the day's start": plan.clock(0), "the file counts": written_in()}
    stale = [name for name, text in pins.items() if text not in readme]
    print(f"  README carries {len(pins) - len(stale)} of {len(pins)} values read from the code: {', '.join(pins)}")
    require(not stale, f"the README no longer matches the code on: {', '.join(stale)}")
    miscounted = readme.replace(" Python files", "0 Python files", 1)
    require(miscounted != readme and "the file counts" in [n for n, t in pins.items() if t not in miscounted],
            "control: a README with a wrong Python file count passed")
    print("  control: a README naming the wrong number of Python files is refused")
    supplied = list(pins.values())
    faults = doc_faults(readme, walkthrough, tracked, supplied)
    print(f"  every number in the README and walkthrough sits in a phrase the code writes; the walkthrough names "
          f"{len(tracked)} published files; faults: {faults or 'none'}")
    require(not faults, "; ".join(faults))
    # Each planted fault must be refused by its own rule, so the fault found has to name what was planted.
    planted = {"a percent typed into the README": (readme + "\nFixes 86.4% of calls.", walkthrough, tracked, "86.4%"),
               "a dollar figure typed into the README": (readme + "\nSaves $12 a call.", walkthrough, tracked, "$12"),
               "a bare number, one the README holds in another place":
                   (readme + f"\nCuts repeat visits by {world.TEST_DAYS} points.", walkthrough, tracked, f"{world.TEST_DAYS}"),
               "a multiple spelled as words":
                   (readme, walkthrough + "\nThe gain is three times the control's.", tracked, "three times"),
               "a new file the walkthrough never names":
                   (readme, walkthrough, tracked + ["staple/planted.py"], "staple/planted.py"),
               "a link to a missing file": (readme + "\n[x](nowhere.md)", walkthrough, tracked, "nowhere.md")}
    missed = [kind for kind, (*docs_, names) in planted.items() if names not in "; ".join(doc_faults(*docs_, supplied))]
    print(f"  control: {len(planted) - len(missed)} of {len(planted)} planted faults refused, each by its own rule")
    require(not missed, f"the docs check passed a planted fault: {missed}")


def example_faults(found):
    """Each example whose hand answer disagrees with what the engine's own function computes."""
    from staple.examples import agrees
    out = []
    for x in found:
        got = x.compute()
        if not agrees(got, x.by_hand):
            out.append(f"{x.id}: worked by hand to {x.by_hand!r}, but {x.source} gives {got!r}")
    return out


EXAMPLE_ORDER = ('class="ex-words"', 'class="ex-inputs', 'class="ex-steps"', 'class="ex-answer"', 'class="formula"')


def order_faults(page, found):
    """Each example not drawn once, inside its own section, as words, inputs, steps, Staple's answer, then any formula."""
    out = []
    for x in found:
        start = page.find(f'<div class="example" id="{x.id}">')
        section = page.find(f'<section id="{x.section}">')
        if page.count(f'id="{x.id}"') != 1 or not -1 < section < start < page.find("</section>", section):
            out.append(f"{x.id}: not drawn once inside its section")
            continue
        end = min(i for i in (page.find('<div class="example"', start + 1), page.find("</section>", start)) if i > -1)
        block, at = page[start:end], 0
        for part in EXAMPLE_ORDER[:5 if x.formula else 4]:
            nxt = block.find(part, at)
            if nxt < 0:
                out.append(f"{x.id}: {part.split(chr(34))[1]} missing or out of order")
                break
            at = nxt
        if x.show(x.compute()) not in html_text(block):
            out.append(f"{x.id}: the page does not print the engine's own answer")
    return out


@check("examples")
def examples_check():
    import dataclasses
    from staple import examples, report
    faults = example_faults(examples.EXAMPLES)
    print(f"  {len(examples.EXAMPLES)} worked examples recomputed by the engine's own functions; "
          f"{len(examples.EXAMPLES) - len(faults)} agree with the hand answer")
    require(not faults, "; ".join(faults))
    first = examples.EXAMPLES[0]
    nudged = dataclasses.replace(first, by_hand=first.by_hand + 0.001)
    require(example_faults([nudged]), f"control: {first.id} with its hand answer nudged by 0.001 passed")
    print(f"  control: {first.id} with its hand answer nudged from {first.by_hand} to {nudged.by_hand} is refused")
    _, method = report.build_manuals(report.NO_HISTORY, None)
    shown = order_faults(method, examples.EXAMPLES)
    print(f"  methods page: {len(examples.EXAMPLES) - len(shown)} of {len(examples.EXAMPLES)} examples in their sections, "
          "as words, inputs, steps, Staple's answer, then the formula")
    require(not shown, "; ".join(shown))
    block = re.search(rf'<div class="example" id="{first.id}">.*?(?=<div class="example"|</section>)', method, re.S).group(0)
    formula = re.search(r'<p class="ex-lead">.*?</code>', block, re.S).group(0)
    turned = block.replace(formula, "").replace('<p class="ex-words">', formula + '<p class="ex-words">', 1)
    require(order_faults(method.replace(block, turned, 1), [first]), "control: a formula drawn before its words passed")
    print(f"  control: {first.id} with its formula drawn before its words is refused")


def dead_links(pages):
    """Every link to an anchor, on one built page or between them, that names an id its page does not hold."""
    from staple import report
    named = {report.FILES[k]: page for k, page in pages.items()}
    out = []
    for kind, page in pages.items():
        for target, anchor in re.findall(r'href="([\w-]+\.html)?#([^"]+)"', page):
            if target in report.FILES.values() and f'id="{anchor}"' not in named.get(target or report.FILES[kind], ""):
                out.append(f"{report.FILES[kind]} to {target or 'itself'}#{anchor}")
            elif not target and f'id="{anchor}"' not in page:
                out.append(f"{report.FILES[kind]} to itself#{anchor}")
    return out


@check("links")
def links():
    from staple import report
    manual, method = report.build_manuals(report.NO_HISTORY, None)
    pages = {"manual": manual, "method": method}
    into = re.findall(rf'href="{report.FILES["method"]}#([^"]+)"', manual)
    dead = dead_links(pages)
    print(f"  {len(into)} links from the manual into the methods page, "
          f"{sum(len(re.findall(r'href=.#', p)) for p in pages.values())} within a page; {len(dead)} lead nowhere")
    require(into and not dead, "links that lead nowhere: " + ", ".join(dead))
    planted = manual.replace(f'href="{report.FILES["method"]}#{into[0]}"', f'href="{report.FILES["method"]}#ex-nowhere"', 1)
    require(dead_links({"manual": planted, "method": method}) == [f"manual.html to method.html#ex-nowhere"],
            "control: a manual link to an anchor the methods page lacks passed")
    print(f"  control: a manual link turned from #{into[0]} to #ex-nowhere is refused")


def house_map(text):
    """The house map's two tables, read as text, because running the script writes its own inventory."""
    tables = []
    for name in ("CODE", "FORMATS"):
        body = re.search(rf"export const {name} = \{{(.*?)\n\}}", text, re.S).group(1)
        tables.append({a or b: v for a, b, v in re.findall(r"(?:'([^']*)'|(\w+))\s*:\s*'([^']*)'", body)})
    return tables


def house_disagreements(code, formats, house_code, house_formats):
    """Where Staple's map and the house map name a kind differently: another language, or code against a format."""
    faults = [f".{k} is {lang} here but {house_code.get(k) or house_formats.get(k) or 'unnamed'} in the house map"
              for k, lang in code.items() if house_code.get(k) != lang.lower()]
    return faults + [f".{k} is a format here but {house_code[k]} code in the house map" for k in formats if k in house_code]


SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}


def programs_started(source):
    """The programs a Python source starts through subprocess: a literal name, the name given to shutil.which, or the
    variable that holds the path. Python itself, as sys.executable, is left out."""
    # ponytail: it reads Python's subprocess calls only, so a Bash script that starts a new program passes; reading
    # each Bash line's first word against the shell's own commands is the upgrade.
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SUBPROCESS_CALLS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            continue
        first = node.args[0].elts[0] if node.args and isinstance(node.args[0], ast.List) and node.args[0].elts else None
        first = first.values[0] if isinstance(first, ast.BoolOp) else first
        first = first.args[0] if isinstance(first, ast.Call) and first.args else first
        if isinstance(first, ast.Attribute) and ast.unparse(first) == "sys.executable":
            continue
        name = first.value if isinstance(first, ast.Constant) else first.id if isinstance(first, ast.Name) else None
        found.add(str(name).lower() if name else f"a command at line {node.lineno} the scan cannot read")
    return found


def program_faults(started):
    from staple.report import PROGRAMS
    return ([f"the code starts {p} with no purpose line in report.PROGRAMS" for p in sorted(started - set(PROGRAMS))]
            + [f"report.PROGRAMS has a line for {p}, which nothing starts" for p in sorted(set(PROGRAMS) - started)])


def section_faults(counts):
    """Each language the repository holds that no place on the code languages page describes."""
    from staple.report import LANGUAGE_ABOUT, PLACES
    placed = {lang for _, _, langs, _ in PLACES for lang in langs}
    return [f"{lang} has {n} files and no section on the code languages page" for lang, n in counts.items()
            if n and not (lang in placed and lang in LANGUAGE_ABOUT)]


def json_faults(data, page):
    """Where the published counts disagree with the code languages page: a count, or the sentence."""
    made = re.search(r'<section id="made-of">.*?</section>', page, re.S)
    shown = {lang: int(n) for lang, n in re.findall(r"<tr><td>([^<]+)</td><td>(\d+)</td>", made.group(0))} if made else {}
    counts = {k: v for k, v in data.get("counts", {}).items() if v}
    faults = [] if shown == counts else [f"counts {counts} against the page's {shown}"]
    return faults + ([] if data.get("sentence") and data["sentence"] in html_text(page) else ["a sentence the page does not carry"])


@check("languages")
def languages_check():
    import json
    import warnings
    from staple import report
    files = report.repo_files()
    found = report.languages(files)
    counts = {lang: len(p) for lang, p in found["code"].items()}
    print(f"  {len(files)} files git would publish; code outside docs/: " + ", ".join(f"{n} {lang}" for lang, n in counts.items()))
    print("  not code: " + ", ".join(f"{len(p)} {k}" for k, p in found["formats"].items())
          + f"; the published copy in docs/: {len(found['published'])}")
    require(not found["unknown"], f"file kinds no map names: {found['unknown']}; name each in report.CODE_LANGUAGES or FORMATS")
    require(report.languages(files + ["staple/planted.rb"])["unknown"] == ["staple/planted.rb"],
            "control: a planted .rb file was not refused")
    print("  control: a planted .rb file is refused as a kind no map names")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, warnings.catch_warnings(record=True) as said:
        warnings.simplefilter("always")
        for name in ("keep.py", "out/skip.py", "skip.pyc", ".gitignore"):
            (Path(tmp) / name).parent.mkdir(exist_ok=True)
            (Path(tmp) / name).write_text("out/\n*.pyc\n" if name == ".gitignore" else "", encoding="utf-8")
        walked = report.repo_files(Path(tmp), env=clean_git_env())
    require(walked == [".gitignore", "keep.py"] and any("walked" in str(w.message) for w in said),
            f"control: a folder with no git gave {walked} and {len(said)} warnings")
    print("  control: a folder with no git is walked past what .gitignore names, and the run warns that it was")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        scratch_history(Path(tmp))("kept.py", "2026-01-01T12:00:00")
        (Path(tmp) / "scratch.py").write_text("", encoding="utf-8")
        listed = report.repo_files(Path(tmp), env=clean_git_env())
    require(listed == ["kept.py"], f"control: a folder holding one saved file and one scratch file gave {listed}")
    print("  control: a scratch file not yet added to git is left out of the list, so it changes no count")

    sections = section_faults(counts)
    print(f"  code languages page: {len(counts) - len(sections)} of {len(counts)} languages described where they run")
    require(not sections, "; ".join(sections))
    require(section_faults({**counts, "Ruby": 1}) == ["Ruby has 1 files and no section on the code languages page"],
            "control: a language with no section passed")
    print("  control: a language held with no section is refused")
    sources = [p for p in code_files() + [ROOT / "check.py"] if p.suffix == ".py"]
    started = set().union(*(programs_started(p.read_text(encoding="utf-8")) for p in sources))
    unnamed = program_faults(started)
    print(f"  programs the Python code starts: {', '.join(sorted(started))}; {len(unnamed)} faults")
    require(not unnamed, "; ".join(unnamed))
    planted = programs_started('import subprocess\nsubprocess.run(["ffmpeg", "-i", "in.mp4"])\n')
    require(program_faults(started | planted) == ["the code starts ffmpeg with no purpose line in report.PROGRAMS"],
            f"control: a planted start of ffmpeg was read as {planted}")
    print("  control: a planted start of ffmpeg with no purpose line is refused")

    page, data = (report.PUBLISHED / n for n in (report.FILES["languages"], report.LANGUAGES_JSON))
    require(page.exists() and data.exists(), f"docs/ has no {page.name} or {data.name}; run python run.py --pages")
    page, data = page.read_text(encoding="utf-8"), json.loads(data.read_text(encoding="utf-8"))
    require(data == report.languages_json(found), "docs/languages.json differs from a fresh count; run python run.py --pages")
    disagree = json_faults(data, page)
    print(f"  docs/{report.LANGUAGES_JSON}: matches a fresh count; against the published page: {disagree or 'agrees'}")
    require(not disagree, "; ".join(disagree))
    bent = dict(data, counts={**data["counts"], "Python": data["counts"]["Python"] + 1})
    require(json_faults(bent, page), "control: a JSON with one count off passed against the page")
    print("  control: a JSON with the Python count one off is refused against the page")

    if not HOUSE_MAP.exists():
        print(f"  WARN house map: {HOUSE_MAP} is not on this machine, so Staple's map was not compared with it")
        return
    house = house_map(HOUSE_MAP.read_text(encoding="utf-8"))
    faults = house_disagreements(report.CODE_LANGUAGES, report.FORMATS, *house)
    alone = sorted(f".{k}" if k else "no extension" for k in report.FORMATS if k not in house[0] and k not in house[1])
    print(f"  against the house map: {len(faults)} disagreements; kinds only Staple names: {', '.join(alone) or 'none'}")
    require(not faults, "; ".join(faults))
    require(house_disagreements({**report.CODE_LANGUAGES, "sh": "PowerShell"}, report.FORMATS, *house),
            "control: a map calling .sh PowerShell agreed with the house map")
    print("  control: a map that calls .sh PowerShell is refused")


def clean_git_env():
    """The environment without the variables that point git at one particular repository, so a clone made
    from inside a commit hook cannot write into the commit being made."""
    names = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True).stdout.split()
    return {k: v for k, v in os.environ.items() if k not in names}


DEAD_ADDRESS = "http://127.0.0.1:9"    # nothing listens on the discard port, so a request sent there fails at once


def stranger_env(home, network):
    """The environment a stranger's machine would give a run: an empty home folder, no Claude key, no setting of
    Staple's or Python's own, and none of git's variables for one repository. Without network, every request made
    through Python's web library goes to a dead address; the local stand-in the AI check starts stays reachable.
    The limit: a program that opened a socket by hand would not be stopped, and the import guard holds that no
    module here does."""
    drop = {"ANTHROPIC_API_KEY", "PYTHONUTF8", "PYTHONPATH", "PYTHONIOENCODING"}
    env = {k: v for k, v in clean_git_env().items() if k.upper() not in drop and not k.upper().startswith("STAPLE_")}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(Path(home) / "AppData" / "Roaming"),
               GIT_CEILING_DIRECTORIES=str(Path(home).parent))
    if not network:
        cases = (str.upper,) if os.name == "nt" else (str.upper, str.lower)   # Windows reads one name whatever its case
        for case in cases:
            env.update({case(n): DEAD_ADDRESS for n in ("http_proxy", "https_proxy", "all_proxy")})
            env[case("no_proxy")] = "127.0.0.1,localhost"
    return env


def run_there(folder, env, *args):
    """One Python run in another folder, as (exit code, every line it printed)."""
    r = subprocess.run([sys.executable, *args], cwd=folder, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()


def said(out, pattern):
    """The first printed line that matches, or a note that none did."""
    return next((line.strip() for line in out if re.search(pattern, line)), "no such line")


def whole_run(folder, env, where):
    """The one command, then every check, in another folder. Returns the checks' printed lines."""
    from staple import report
    code, out = run_there(folder, env, "run.py")
    print(f"  python run.py: exit {code}, \"{said(out, r'^done in ')}\"")
    require(code == 0, f"the one command failed {where}: " + " | ".join(out[-5:]))
    missing = [f for f in report.FILES.values() if not (folder / "out" / f).exists()]
    require(not missing, f"the run {where} wrote no {', '.join(missing)}")
    code, out = run_there(folder, env, "check.py")
    print(f"  python check.py: exit {code}, \"{said(out, r'^[0-9]+ of [0-9]+ passed')}\"")
    require(code == 0, f"a check failed {where}: " + " | ".join(line.strip() for line in out if "FAILED" in line))
    return out


def damaged_copy_refused(folder, env, where):
    """The control both whole-run checks share: a published page with one label taken off must fail that folder's
    own report check, or its checks may not be reading the copy at all."""
    path = folder / "docs" / "index.html"
    page = path.read_text(encoding="utf-8")
    path.write_text(page.replace('class="madeup"', 'class="planted"', 1), encoding="utf-8")
    code, _ = run_there(folder, env, "check.py", "report")
    print(f"  control: the report check {where}, on a published copy with a label taken off, exits {code}")
    require(code != 0, f"the checks {where} passed a damaged published copy, so they may not be reading it")


ROOM_PROBE = """import os, urllib.request
from pathlib import Path
print(Path.home())
print(os.environ.get("ANTHROPIC_API_KEY"))
try:
    urllib.request.urlopen("https://example.com", timeout=20)
    print("reached the web")
except OSError:
    print("no web")
"""


@check("first-run", by_name=True)
def first_run():
    """A stranger's first run, offline: every file git would publish is copied to an empty folder, and the one
    command and every check run there with an empty home folder, no key and no web."""
    from staple import report
    files = public_files()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        room, home = Path(tmp) / "staple", Path(tmp) / "home"
        home.mkdir()
        for p in files:
            dest = room / p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
        env = stranger_env(home, network=False)
        print(f"  {len(files)} files git would publish, copied to an empty folder with no git history")
        code, out = run_there(room, env, "-c", ROOM_PROBE)
        print(f"  the room: home folder {out[0] if out else '?'}, Claude key {out[1] if len(out) > 1 else '?'}, "
              f"a web request: {out[2] if len(out) > 2 else '?'}")
        require(code == 0 and out == [str(home), "None", "no web"] and not any(home.iterdir()),
                f"the room is not what a stranger's first run needs: {out}")
        out = whole_run(room, env, "on a first run")
        page = (room / "out" / "index.html").read_text(encoding="utf-8")
        require(f">{report.NO_HISTORY}</span>" in page, "the page built with no git history does not say so in its stamp")
        skipped = [line.strip() for line in out if line.startswith("skipped in ")]
        print(f"  parts the first run could not do, each named: {len(skipped)}")
        for line in skipped:
            print(f"    {line[:150]}")
        require(any("private-names scan" in line for line in skipped),
                "the first run ran the private-names scan, so the author's own list reached the room")
        damaged_copy_refused(room, env, "in the room")


@check("fresh-clone", by_name=True)
def fresh_clone():
    """The publishing check: clone the public repository as a stranger would, run the one command and every
    check there, and hold the clone's fresh build and the live report to the clone's published copy."""
    import urllib.request
    from staple import report
    local = report.git_answer(ROOT, "rev-parse", "HEAD")
    require(local, "this folder holds no git history, so there is no commit to hold the public repository to")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        clone, home = Path(tmp) / "staple", Path(tmp) / "home"
        home.mkdir()
        env = stranger_env(home, network=True)
        cloned = subprocess.run(["git", "clone", "--quiet", REPO_URL, str(clone)], env=env, capture_output=True, text=True)
        require(cloned.returncode == 0, f"the public repository could not be cloned: {cloned.stderr.strip()[:300]}")
        head = report.git_answer(clone, "rev-parse", "HEAD")
        print(f"  cloned {REPO_URL} at {head[:7]}; this copy is at {local[:7]}")
        require(head == local, "the public repository is not at this commit: push first, then run this again")
        whole_run(clone, env, "on a fresh clone")

        page = (clone / "docs" / "index.html").read_text(encoding="utf-8")
        built = (clone / "out" / "index.html").read_text(encoding="utf-8")
        print(f"  the clone's fresh build {'equals' if built == page else 'differs from'} its published copy, stamp included")
        require(built == page, "a fresh clone builds a report that differs from its published copy; rebuild the pages "
                               "on the last commit with `python run.py --pages`, commit docs/ and push")
        live = urllib.request.urlopen(urllib.request.Request(report.PAGES_URL, headers={"Cache-Control": "no-cache"}),
                                      timeout=30).read().decode("utf-8")
        print(f"  live report at {report.PAGES_URL}: {'matches' if live == page else 'differs from'} the clone's published copy")
        require(live == page, "the live report is not the committed copy yet; GitHub Pages may still be rebuilding")
        damaged_copy_refused(clone, env, "in the clone")


def main(argv):
    from staple import report
    for stream in (sys.stdout, sys.stderr):
        # A piped run on Windows would otherwise garble the stamp's middle dot, and would hold every line back
        # until the end, so a long check looked stuck.
        stream.reconfigure(encoding="utf-8", line_buffering=True)
    report.notes_in_plain_words()
    names = argv[1:] or [n for n in CHECKS if n not in BY_NAME]
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        print(f"unknown check(s): {', '.join(unknown)}. Known: {', '.join(CHECKS)}")
        return 2
    failed = []
    for name in names:
        RUNNING[0] = name
        print(f"[{name}]", flush=True)
        try:
            CHECKS[name]()
            print("  ok")
        except CheckFailed as e:
            print(f"  FAILED: {e}")
            failed.append(name)
        except Exception as e:    # a crash is one named failure, and the checks after it still run
            at = traceback.extract_tb(e.__traceback__)[-1]
            print(f"  FAILED: the check crashed with {type(e).__name__}: {e} (at {Path(at.filename).name}, line {at.lineno})")
            failed.append(name)
    passed = len(names) - len(failed)
    print(f"\n{passed} of {len(names)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    for name, what in SKIPPED:
        print(f"skipped in {name}: {what}")
    if not argv[1:]:
        print(f"not run: {', '.join(sorted(BY_NAME))}. Each runs this whole suite in another folder, so it runs only when named.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
