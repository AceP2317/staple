"""Every check for Staple. `python check.py` runs them all; `python check.py <name>` runs one.

Each check prints what it measured and exits non-zero on failure. The controls exist to prove a
check can still fail: a check that never fails looks exactly like one that sees nothing.
"""
import ast
from collections import Counter
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SIZE_CEILING = 2500  # lines of Python and SQL outside the checks; raise it on purpose, never by drift
CODE_GLOBS = ["staple/*.py", "sql/*.sql", "run.py"]
NAMES_FILE = ROOT / ".private" / "banned-names.txt"
PUBLIC_CLONE = os.environ.get("STAPLE_PUBLIC_CLONE") == "1"
REPO_URL = "https://github.com/AceP2317/staple.git"

CHECKS = {}


class CheckFailed(Exception):
    pass


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


# ---- the pieces the guards are built from ---------------------------------------------------

def public_files():
    """Every file git would publish: tracked, plus new files not ignored."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.splitlines() if p.strip() and (ROOT / p).is_file()]


def code_files():
    return sorted(p for g in CODE_GLOBS for p in ROOT.glob(g))


def count_lines(paths):
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in paths)


def over_ceiling(total, ceiling=SIZE_CEILING):
    return total > ceiling


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
    total = count_lines(files)
    print(f"  size: {total} of {SIZE_CEILING} lines across {len(files)} files")
    require(not over_ceiling(total), f"size ceiling passed: {total} > {SIZE_CEILING}")

    outside = {}
    for p in files + [ROOT / "check.py"]:
        if p.suffix == ".py":
            mods = third_party_imports(p.read_text(encoding="utf-8"))
            if mods:
                outside[p.name] = sorted(mods)
    print(f"  packages beyond Python's own: {outside or 'none'}")
    require(not outside, f"third-party imports found: {outside}")

    if PUBLIC_CLONE:
        print("  names scan: SKIPPED on a public clone, which has no private names list (by design)")
    else:
        require(NAMES_FILE.exists(), f"names list missing at {NAMES_FILE}; the scan cannot run")
        names, pub = load_names(), public_files()
        hits = scan_for_names(pub, names)
        print(f"  names scan: {len(names)} names over {len(pub)} public files, {len(hits)} hits")
        require(not hits, "names found in public files: " + "; ".join(hits))

    require(over_ceiling(SIZE_CEILING + 1), "control: the size check passed a count over the ceiling")
    require(third_party_imports("import numpy\nfrom pandas import x\n") == {"numpy", "pandas"},
            "control: the import check missed numpy and pandas")
    require(not third_party_imports("import sqlite3, json\nfrom pathlib import Path\n"),
            "control: the import check flagged Python's own modules")
    print("  controls: the size and import checks both fire on planted input")


@check("banned-control")
def banned_control():
    require(NAMES_FILE.exists(), "names list missing; nothing to plant")
    names = load_names()
    with tempfile.TemporaryDirectory(dir=ROOT / ".private") as tmp:
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
    for table in ("jobs", "job_parts", "techs", "tech_skills", "truck_stock", "centre_stock"):
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
    return _WORLD["conn"]


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


def load_faults(fleet, tech, parts):
    """What is wrong with one truck's load: over its space, a locked part without certification, an unshippable part."""
    faults = []
    if sum(fleet.size[p] for p in parts) > fleet.capacity[tech]:
        faults.append("over its space")
    if tech not in fleet.certified and parts & fleet.locked:
        faults.append("a locked part on an uncertified truck")
    if any(p in fleet.eta and p not in fleet.stock[tech] for p in parts):
        faults.append("a back-ordered part the truck did not already hold")
    return faults


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
    jobs = backtest.test_jobs(conn)
    r = load.run(conn, jobs)
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
    faults = sum(bool(load_faults(fleet, tech, parts)) for row in recorded for (_, tech), parts in row["loads"].items())
    uncertified = next(t for t in fleet.capacity if t not in fleet.certified)
    planted = load_faults(fleet, uncertified, set(fleet.size))
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


@check("backorders")
def backorders_check():
    from staple import backorders, backtest, load
    conn = shared_world()
    jobs = backtest.test_jobs(conn)
    fleet = load.Fleet(conn)
    rows = backorders.run(conn, fleet, jobs)
    for r in rows[:5]:
        print(f"  {r['part']}: arrives in {r['eta']} days; {len(r['holders'])} trucks hold one; "
              f"{r['expected']:.1f} calls expected, {r['stuck']:.1f} stuck (${r['at_risk']:,.0f} at risk); "
              f"{r['actual']} came, {r['actual_stuck']} stuck")
    require(sorted(r["part"] for r in rows) == sorted(fleet.eta), "the list does not hold every back-ordered part once")
    require(all(a["at_risk"] >= b["at_risk"] for a, b in zip(rows, rows[1:])), "the list is not ranked by dollars at risk")
    for name, actual, predicted in (("calls", sum(r["actual"] for r in rows), sum(r["expected"] for r in rows)),
                                    ("stuck calls", sum(r["actual_stuck"] for r in rows), sum(r["stuck"] for r in rows))):
        print(f"  {name} before arrival: forecast {predicted:.1f}, actual {actual}; "
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
    r = triage.run(conn, jobs, cache["answers"], asked["answers"])
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
        g = triage.run(conn, group, cache["answers"], asked["answers"])["rows"]
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
    working after its breakdown, a route running past 17:00."""
    from staple import dispatch
    faults = Counter()
    served = Counter(job for _, _, job, _, _, _ in result["visits"])
    rolled = Counter(result["rolled_jobs"])
    faults["served or rolled other than once"] = sum(1 for j in jobs if served[j["job"]] + rolled[j["job"]] != 1)
    for day, tech, _, start, end, _ in result["visits"]:
        sick, broken = result["events"][day]
        faults["visit by a sick technician"] += tech in sick
        faults["visit on a broken truck after it broke"] += tech in broken and start >= broken[tech]
        faults["visit past 17:00"] += end > dispatch.DAY + 1e-9
    return +faults


@check("dispatch")
def dispatch_check():
    from staple import backtest, dispatch
    conn = shared_world()
    jobs = backtest.test_jobs(conn)
    out = dispatch.run(conn, jobs)
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


@check("dispatch-control")
def dispatch_control():
    from staple import backtest, dispatch, load
    conn = shared_world()
    jobs = backtest.test_jobs(conn)
    out = dispatch.run(conn, jobs)
    same = dispatch.replay(conn, jobs, "skills and parts", "balanced", dispatch.Crew(conn))
    base, parts, staple = (out[n] for n in ("nearest technician, balanced loads", "parts only, balanced loads",
                                            "Staple: skills and parts, balanced loads"))
    skills_off, odds_off = out[dispatch.SHUFFLED_SKILLS], out[dispatch.SHUFFLED_ODDS]
    print(f"  first-visit fix a call: nearest {base['fixes_per_call']:.1%}, parts only {parts['fixes_per_call']:.1%}, "
          f"Staple {staple['fixes_per_call']:.1%}")
    print(f"  shuffled skills {skills_off['fixes_per_call']:.1%}: the skill gain over parts only "
          f"({staple['fixes_per_call'] - parts['fixes_per_call']:+.1%}) becomes "
          f"{skills_off['fixes_per_call'] - parts['fixes_per_call']:+.1%}")
    print(f"  shuffled-history parts odds {odds_off['fixes_per_call']:.1%}: dispatch needs only appliance-level "
          f"parts odds, since at night any truck can load anything but locked or back-ordered parts")
    require(abs(same["fixes_per_call"] - staple["fixes_per_call"]) < 1e-12, "the control's machinery changes the answer by itself")
    side = max(c["rolled"] for c in (skills_off, odds_off)) - staple["rolled"]
    print(f"  the controls roll {side:+} calls beyond Staple's, so neither wins by overbooking the day")
    require(side <= 0.01 * staple["calls"], "a control withdraws the gain by overbooking routes, not by choosing worse")
    require(skills_off["fixes_per_call"] - parts["fixes_per_call"] <= 0.5 * (staple["fixes_per_call"] - parts["fixes_per_call"]),
            "shuffled skills keep most of the skill gain: it may not come from knowing the technicians")
    require(abs(odds_off["fixes_per_call"] - staple["fixes_per_call"]) < 0.01,
            "model-level parts odds now matter to dispatch: re-read the claim that appliance-level odds suffice")


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

def main(argv):
    names = argv[1:] or list(CHECKS)
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        print(f"unknown check(s): {', '.join(unknown)}. Known: {', '.join(CHECKS)}")
        return 2
    failed = []
    for name in names:
        print(f"[{name}]")
        try:
            CHECKS[name]()
            print("  ok")
        except CheckFailed as e:
            print(f"  FAILED: {e}")
            failed.append(name)
    passed = len(names) - len(failed)
    print(f"\n{passed} of {len(names)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
