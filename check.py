"""Every check for Staple. `python check.py` runs them all; `python check.py <name>` runs one.

Each check prints what it measured and exits non-zero on failure. The controls exist to prove a
check can still fail: a check that never fails looks exactly like one that sees nothing.
"""
import ast
import atexit
from collections import Counter, defaultdict
import contextlib
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
STAND_INS = ["examplecorp", "example-tool", "example map"]    # tried in place of the private list where a copy has none

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


def find_bash():
    """A bash that runs: on Windows the one beside git, because a bare "bash" there can start another system; else
    the one on the path. None where neither answers."""
    git = shutil.which("git")
    beside = [up / "bin" / "bash.exe" for up in list(Path(git).resolve().parents)[1:3]] if os.name == "nt" and git else []
    for bash in [str(p) for p in beside if p.exists()] + [shutil.which("bash")]:
        if bash and subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True).stdout.strip() == "ok":
            return bash
    return None


def runner_faults(bash, source, brief=False):
    """Where a guard runner breaks its contract, tried on a scratch folder of planted guards: its arguments, its
    three exit codes, the line it prints for each result, and the guards a commit's staged files select. brief
    stops after the passing and the failing guard, which is all the control needs."""
    faults = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        # The runner's own switches are taken out, so a run started from a commit hook tries the same cases.
        root, env = Path(tmp), {k: v for k, v in clean_git_env().items() if not k.startswith("GUARD_")}
        guards = root / "scripts" / "guards"
        (root / "scripts").mkdir()
        (root / "scripts" / "guard.sh").write_text(source, encoding="utf-8", newline="\n")

        def plant(tier, **scripts):
            shutil.rmtree(guards, ignore_errors=True)
            (guards / tier).mkdir(parents=True)
            for name, body in scripts.items():
                (guards / tier / f"{name}.sh").write_text(body + "\n", encoding="utf-8", newline="\n")

        def expect(what, code, *words, args=(), **extra):
            r = subprocess.run([bash, str(root / "scripts" / "guard.sh"), *args], capture_output=True, text=True,
                               env={**env, **extra})
            said = r.stdout + r.stderr
            if r.returncode != code:
                faults.append(f"{what}: exit {r.returncode} where {code} was due")
            elif not all(w in said for w in words):
                faults.append(f"{what}: its lines lack {[w for w in words if w not in said]}")
            return said

        plant("fast", passes="exit 0")
        expect("a guard that passes", 0, "  ok ", "1 test(s) run, 1 passed, 0 failed")
        expect("an argument it does not know", 2, "usage:", args=["--nope"])
        expect("a folder of guards that is absent", 0, "no nightly tier", args=["--nightly"])
        plant("fast", passes="exit 0", fails="echo a planted fault; exit 1")
        expect("a guard that fails", 1, "  FAIL ", "a planted fault", "2 test(s) run, 1 passed, 1 failed")
        if brief:
            return faults
        plant("fast")
        expect("a folder of guards that holds none", 1, "HOLDS NO GUARDS")
        plant("quarantine", mended="exit 0")
        expect("a parked guard that now passes", 2, "  PASSES ", args=["--quarantine"])
        plant("quarantine", parked="echo this mistake is present; exit 1")
        expect("a parked guard that still finds its mistake", 0, "  known ", args=["--quarantine"])
        plant("quarantine", broken="exit 1")
        expect("a parked guard that is broken", 1, "  FAIL ", args=["--quarantine"])
        plant("fast", slow="sleep 1")
        (guards / "fast-budget").write_text("1  a planted allowance\n", encoding="utf-8", newline="\n")
        expect("a guard slower than its allowance", 0, "slower than the 1ms")
        plant("fast", hangs="sleep 8")
        said = expect("a guard that hangs", 1, "  KILLED ", GUARD_KILL_FAST="1")
        if "no GNU timeout" in said:    # this machine cannot stop a guard, and the runner said so; the rule is not tried here
            faults = [f for f in faults if not f.startswith("a guard that hangs")]
            skip("the stop for a guard that hangs, which needs GNU timeout")

        (root / "docs").mkdir()
        (root / "docs" / "page.html").write_text("staged", encoding="utf-8")
        for args in (["init", "-q"], ["add", "docs/page.html"]):
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env)
            require(done.returncode == 0, f"git {args[0]} failed in the scratch folder: {done.stderr.strip()[:200]}")
        plant("fast", pages="# COVERS: docs/*.html\nexit 0", queries="# COVERS: sql/*\nexit 1", always="exit 0")
        expect("a commit that stages a page", 0, "2 test(s) run, 2 passed, 0 failed", "1 fast test(s) skipped", GUARD_SCOPE="staged")
        expect("the same guards with no commit", 1, "3 test(s) run, 2 passed, 1 failed")
    return faults


@check("plain-words")
def plain_words():
    """The public copy holds no word from the author's private setup, every entry of the private list can be seen
    by the scan, and the guard runner keeps its arguments, its exit codes and its result lines."""
    if NAMES_FILE.exists():
        names, scratch, pub = load_names(), ROOT / ".private", public_files()
        hits = scan_for_names(pub, names)
        print(f"  {len(names)} private names and words over {len(pub)} public files: {len(hits)} hits")
        require(not hits, "private names or words in public files: " + "; ".join(hits))
    else:
        names, scratch = STAND_INS, None
        skip("the private-words scan. Its list is kept out of the public repository, so only the author's copy holds "
             "one; the scan itself is tried below on stand-in words")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        copy = Path(tmp) / "README.md"
        copy.write_text(readme, encoding="utf-8")
        clean = scan_for_names([copy], names)
        unseen = []
        for n, name in enumerate(names, 1):
            copy.write_text(readme + f"\nA line that names {name} once.\n", encoding="utf-8")
            if not scan_for_names([copy], names):
                unseen.append(n)
    print(f"  a copy of the README: {len(clean)} hits as it stands; each of the {len(names)} entries planted in it in turn: "
          f"{len(names) - len(unseen)} caught")
    require(not clean, "the copy of the README is not clean before anything is planted")
    require(not unseen, f"the scan cannot see {len(unseen)} of its own entries when planted: entries {unseen} of the list")
    require(find_names("see the Example Map here", STAND_INS) == ["example map"], "control: the scan missed a word of two parts")
    require(not find_names("examples and maps, and an example-toolkit", STAND_INS),
            "control: the scan matched a stand-in inside other words")
    print("  control: a word of two parts is caught as a whole, and is not matched inside other words")

    bash = find_bash()
    if not bash:
        skip("the guard runner's own test, which needs bash")
        return
    runner = (ROOT / "scripts" / "guard.sh").read_text(encoding="utf-8")
    faults = runner_faults(bash, runner)
    print(f"  the guard runner on planted guards: passing, failing, unknown argument, absent and empty folders, three parked "
          f"guards, a slow one, a hanging one, and guards chosen by staged files: {len(faults)} faults")
    require(not faults, "the guard runner breaks its contract: " + "; ".join(faults))
    gate = '[ "$failed" -eq 0 ] || exit 1'
    require(runner.count(gate) == 1, "the runner no longer holds the line this control takes out")
    loose = runner_faults(bash, runner.replace(gate, ""), brief=True)
    require(any(f.startswith("a guard that fails") for f in loose), "control: a runner that lets a failing guard through passed")
    print("  control: a copy of the runner that exits 0 on a failing guard is refused")


# ---- the made-up world ------------------------------------------------------------------------

def built_world(path):
    from staple import db, world
    conn = db.fresh(path)
    world.build(conn)
    return conn


def digest(conn):
    import hashlib
    h = hashlib.sha256()
    for table in ("jobs", "job_parts", "techs", "tech_skills", "truck_stock", "backorders", "waits"):
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
        answers, _WORLD["followups"] = triage.load(conn)
        _WORLD["week"] = plan.build(conn, answers, _WORLD["followups"])
    return _WORLD["week"], _WORLD["followups"]


def recorded_replay(name):
    """One row of the dispatch table replayed again with its visits and loads recorded, as (the held-back jobs, the
    run). Each takes several seconds, so a check run makes each once and its checks share it."""
    from staple import backtest, dispatch, load
    conn = shared_world()
    if "plans" not in _WORLD:
        _WORLD["held_back"] = backtest.test_jobs(conn)
        load.mark_unplanned(_WORLD["held_back"])
        _WORLD["plans"], _WORLD["replays"] = dispatch.plans(conn, _WORLD["held_back"]), {}
    if name not in _WORLD["replays"]:
        _WORLD["replays"][name] = dispatch.replay(conn, _WORLD["held_back"], record=True, **_WORLD["plans"][name])
    return _WORLD["held_back"], _WORLD["replays"][name]


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
                                              "from the baseline: the gain may be an artifact")
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


@contextlib.contextmanager
def stand_in_api(reply, status=200):
    """A local stand-in for the Claude API, up for as long as its block runs. It records the requests it gets and
    answers each with reply: a JSON value, raw text, or a function of the request's body that returns either."""
    import http.server
    import json
    import threading
    seen = {"requests": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
            seen["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen["requests"] += 1
            answer = reply(seen["body"]) if callable(reply) else reply
            data = answer.encode() if isinstance(answer, str) else json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/messages", seen
    finally:
        server.shutdown()
        server.server_close()


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
    for name in ("booked code", "Claude", "Claude, top guess only", triage.ASKED, triage.RANDOM_REPLIES, triage.KEYWORDS,
                 "no code", "Claude answers shuffled (control)"):
        row = rows[name]
        print(f"  {name}: agrees with the booked code {row['agree_booked']:.1%}, true problem "
              f"{row['agree_true']:.1%}, 3-part list covers {row['coverage']:.1%}")
    claude, rules = rows["Claude"], rows[triage.KEYWORDS]
    require(claude["agree_booked"] > rules["agree_booked"] + 0.10 and claude["coverage"] > rules["coverage"],
            "Claude does not clearly beat the keyword matcher")
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
    gain = rows[triage.ASKED]["coverage"] - claude["coverage"]
    noise = rows[triage.RANDOM_REPLIES]["coverage"] - claude["coverage"]
    print(f"  follow-up asked on {r['asked']:,} bookings, the customer's problem offered on {r['settled']:,}; "
          f"coverage {gain:+.1%} with ideal replies, {noise:+.1%} with random ones")
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
    with stand_in_api({"content": [{"type": "text", "text": good}], "stop_reason": "end_turn"}) as (url, seen):
        got = triage.parse(triage.ask_api("a prompt", "test-key", url=url), problems, ["it drips"])
    require(seen["headers"].get("x-api-key") == "test-key" and seen["headers"].get("anthropic-version") == triage.API_VERSION
            and seen["body"]["model"] == triage.MODEL and seen["body"]["output_config"] == {"effort": triage.EFFORT},
            "live mode does not send the documented request")
    require(got == {"it drips": {"code": "LEAK", "confidence": 0.9}}, "live mode misreads a reply")
    refused = False
    with stand_in_api({"content": [], "stop_reason": "refusal", "stop_details": {"category": None}}) as (url, _):
        try:
            triage.ask_api("a prompt", "test-key", url=url)
        except triage.LiveError:
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
        print(f"  {name}: first-time fix {r['fixes_per_call']:.1%} of calls; parts missing {r['parts_missing']:.1%}; "
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
    print(f"  first-time fix a call: nearest {base['fixes_per_call']:.1%}, parts only {parts['fixes_per_call']:.1%}, "
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
    side = max(c["rolled"] for c in (skills_off, odds_off, out[dispatch.SHUFFLED_HISTORY])) - staple["rolled"]
    print(f"  the controls roll {side:+} calls beyond Staple's, so none wins by overbooking the day")
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
    own = thin.execute("SELECT COUNT(*), SUM(first_time_fix) FROM jobs WHERE split = 'train' AND parts_on_truck = 1 "
                       "AND tech = ? AND type = ?", pair).fetchone()
    group = thin.execute("SELECT COUNT(*), SUM(first_time_fix) FROM jobs WHERE split = 'train' AND parts_on_truck = 1 "
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
    rewritten.execute("UPDATE jobs SET first_time_fix = 0 WHERE split = 'train' AND tech = ? AND type = ?", best)
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
        print(f"  {d['day']}: {int(o['calls'])} calls; first-time fix {o['fixes'] / o['calls']:.1%} against "
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
    from staple import backorders, backtest, buyer, dispatch, load, triage
    for table, fill in (("backtest", lambda: backtest.run(conn)),
                        ("load_policy", lambda: load.run(conn)),
                        ("backorder_list", lambda: backorders.run(conn)),
                        ("buyer_prices", lambda: buyer.run(conn)),
                        ("triage", lambda: triage.run(conn, *triage.load(conn))),
                        ("dispatch", lambda: dispatch.run(conn))):
        if not one(conn, f"SELECT COUNT(*) FROM {table}"):
            fill()


def to(value, places):
    """A recomputed value beside the decimal places its query shows, so the comparison knows what rounding can do."""
    return (value, places)


def hand_tables(conn, stock_as_replayed=True):
    """Every column of every metric query recomputed in Python, apart from the SQL, as {query: {row: {column: value}}}.
    The four history measures and the history row of the cycle-time query are counted again from the jobs, the parts
    they needed, the truck stock, the back-orders and the recorded days. The others
    report a results table, so its raw rows are read and the query's arithmetic is redone. The limit: for those
    this guards the query and not the replay that filled the table, which the engine's own checks hold. One row
    goes further: the return on today's trucks is counted again from the jobs and the stock. Pass
    stock_as_replayed=False where the stock was changed after the replay ran, and that recount is left out."""
    from datetime import date, timedelta
    from staple import triage

    def pct(share):
        return None if share is None else to(100 * share, 1)

    def of(part, whole):
        return pct(part / whole) if whole else None    # SQL gives NULL for a share of nothing

    jobs = conn.execute("SELECT job, day, type, tech, parts_on_truck, first_time_fix FROM jobs").fetchall()
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
        "Staple on shuffled history (control)": control(
            "the whole plan over the current rules", first, 1,
            *(fixes[p] for p in ("nearest technician, today's stock", "Staple: skills and parts, balanced loads",
                                 "Staple on shuffled history (control)"))),
        "Claude answers shuffled (control)": control(
            "Claude reading the customer's words", list_covers, 1,
            *(read[m] for m in ("no code", "Claude", "Claude answers shuffled (control)"))),
        triage.RANDOM_REPLIES: control(
            "Claude asking one question when unsure", list_covers, 1,
            *(read[m] for m in ("Claude", triage.ASKED, triage.RANDOM_REPLIES))),
    }
    said = len(set(conn.execute("SELECT type, words FROM jobs WHERE split = 'test'").fetchall()))
    flag = dict(conn.execute("SELECT measure, value FROM availability WHERE kind = 'jobs'"))
    sure_from = one(conn, "SELECT value FROM assumptions WHERE name = 'ai_confidence'")
    # Parts cycle time. The history row is counted again from the held-back jobs, their parts, the truck stock, the
    # back-orders and the recorded days. A policy's row redoes the query's arithmetic on the replay's raw figures.
    came_back = dict(conn.execute("SELECT job, repeat_visit_day FROM waits"))
    calendar = defaultdict(list)
    for part, starts, ships in conn.execute("SELECT part, starts, ships FROM backorders"):
        calendar[part].append((starts, ships))
    late = [(job, day, tech) for job, day, _, tech, *_ in jobs if day >= test_from]
    waited = [(date.fromisoformat(came_back[job]) - date.fromisoformat(day)).days for job, day, _ in late if job in came_back]
    on_backorder = sum(1 for job, day, tech in late if job in came_back
                       and any(a <= day < b for part in needed[job] - aboard[tech] for a, b in calendar[part]))
    cycle = {"history as recorded": {
        "repairs": len(late), "repairs_waiting_pct": of(len(waited), len(late)),
        "waits_on_backorder_pct": of(on_backorder, len(waited)),
        "days_when_waiting": to(sum(waited) / len(waited), 2) if waited else None,
        "days_a_repair": to(sum(waited) / len(late), 2)}}
    cycle.update({policy: {"repairs": served, "repairs_waiting_pct": pct(lacking),
                           "waits_on_backorder_pct": of(stuck_share, lacking),
                           "days_when_waiting": to(wait / lacking, 2) if lacking else None, "days_a_repair": to(wait, 2)}
                  for policy, served, lacking, stuck_share, wait in
                  conn.execute("SELECT policy, served, parts_missing, backorder_missing, wait_per_call FROM dispatch")})
    # The scorecard: the query's arithmetic redone on the replay's raw figures for its three plans.
    raw = {policy: figures for policy, *figures in conn.execute(
        "SELECT policy, fixes_per_call, parts_missing, line_fill, wait_per_call, part_jobs, stock_value FROM dispatch")}

    def five(policy):
        fix, lacking, fill, wait, part_jobs, stock = raw[policy]
        return (100 * fix, 100 * lacking, 100 * fill, wait, part_jobs * repeat_visit * 365 / stock)

    trio = [five(p) for p in ("nearest technician, today's stock", "Staple: skills and parts, balanced loads",
                              "Staple on shuffled history (control)")]
    score = {}
    for i, (name, unit, better, digits) in enumerate((
            ("first-time fix", "percent of calls", "higher", 1), ("parts-caused reschedules", "per 100 calls served", "lower", 1),
            ("fill rate", "percent of part lines", "higher", 1), ("parts cycle time", "days a repair", "lower", 2),
            ("inventory return", "dollars a year per dollar of stock", "higher", 2))):
        now, ours, broken = (round(plan[i], digits) for plan in trio)
        score[name] = {"unit": unit, "better_when": better, "current_rules": to(trio[0][i], digits),
                       "staple": to(trio[1][i], digits), "difference": to(ours - now, digits),
                       "control": to(trio[2][i], digits), "control_keeps_pct": to(100 * (broken - now) / (ours - now), 0)}
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
            method: {"jobs": n, "phrasings": said, "agrees_booked_pct": pct(booked), "agrees_true_pct": pct(true),
                     "coverage_pct": pct(covered)}
            for method, n, booked, true, covered in
            conn.execute("SELECT method, jobs, agree_booked, agree_true, coverage FROM triage")},
        "backorder_flag": {
            int(flag["jobs"]): {"forecast_stuck": to(flag["expected"], 1), "were_stuck": int(flag["blocked"]),
                                "flagged": int(flag["flagged"]), "flagged_and_stuck": int(flag["caught"]),
                                "flags_right_pct": of(flag["caught"], flag["flagged"])}},
        "ai_calibration": {
            f"{low:.2f} to {high:.2f}": {"phrasings": band_said, "jobs": n, "agrees_booked_pct": pct(booked),
                                         "agrees_true_pct": pct(true),
                                         "staple_then": "runs on the code" if low >= sure_from else "asks its question"}
            for low, high, band_said, n, booked, true in
            conn.execute("SELECT low, high, phrasings, jobs, agree_booked, agree_true FROM triage_calibration")},
        "dispatch_scorecard": {
            policy: {"calls": calls, "rolled": rolled, "first_time_fix_pct": pct(fixes), "parts_missing_pct": pct(missing),
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
        "parts_cycle_time": cycle,
        "scorecard": score,
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
        copy.execute("UPDATE jobs SET first_time_fix = 0 WHERE substr(day, 1, 7) = ?", (first,))
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
    "ai_calibration": ("c.low >= a.value", "c.low > a.value", "has Staple ask its question at the very confidence it runs on"),
    "ai_step": ("100.0 * agree_true", "100.0 * agree_booked",
                "reports agreement with the booked code as agreement with the true problem"),
    "backorder_exposure": ("ROUND(at_risk, 0)", "ROUND(expected_stuck, 0)", "reports stuck calls as dollars at risk"),
    "backorder_flag": ("measure = 'blocked'", "measure = 'flagged'", "reports the flagged bookings as the stuck ones"),
    "carry_list_curve": ("WHERE k > 0", "WHERE k > 1", "drops the one-part carry list"),
    "dispatch_scorecard": ("100.0 * fixes_per_call", "100.0 * moved_fix_rate",
                           "reports the moved calls' fix rate as every call's"),
    "first_time_fix": ("AVG(first_time_fix)", "AVG(parts_on_truck)", "counts a visit with its parts aboard as a fix"),
    "controls": ("< a.value", ">= a.value", "calls a control that kept its gain fallen back, and one that lost it kept"),
    "inventory_roi": ("l.saved * a.value", "l.coverage * l.jobs * a.value",
                      "counts a job that needed no part as a repeat visit the stock avoided"),
    "parts_caused_reschedules": ("AND parts_on_truck = 0) / COUNT(*)", "AND parts_on_truck = 1) / COUNT(*)",
                                 "counts the repeats that had their parts as caused by parts"),
    "parts_cycle_time": ("julianday(j.day)", "julianday(w.order_day)",
                         "counts a wait from the day the part was ordered, not from the visit that lacked it"),
    "scorecard": ("100.0 * d.line_fill", "100.0 * (1 - d.parts_missing)",
                  "reports the share of calls with every part aboard as the share of part lines aboard"),
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
                "ai_step": ("triage", "method"), "backorder_exposure": ("backorder_list", "day, part"),
                "parts_cycle_time": ("dispatch", "policy")}
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


def changed(results, query, key, column, value):
    """The query results with one column of one row changed, for a page built from a wrong figure."""
    columns, rows = results[query]
    at = columns.index(column)
    return dict(results, **{query: (columns, [r[:at] + (value(r[at]),) + r[at + 1:] if r[0] == key else r for r in rows])})


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
        "the parts-odds control": ("dispatch_scorecard", [report.pct(scores[odds]["first_time_fix_pct"]),
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
    whole = sorted({table for table, _ in WHOLE_TABLES.values()})
    held = sum(one(conn, f"SELECT COUNT(*) FROM {table}") for table in whole)
    print(f"  {held:,} rows in the {len(whole)} results tables that {len(WHOLE_TABLES)} queries report whole: {len(hidden)} left out; "
          f"{len(results['controls'][1])} controls, {len(unlisted)} without a row in the controls query")
    require(not hidden, "a query hides rows of its results table: " + "; ".join(hidden[:3]))
    require(not unlisted, f"a control has no row in the controls query: {unlisted}")
    text = (metrics.SQL / "inventory_roi.sql").read_text(encoding="utf-8")
    cur = conn.execute(text.replace("ORDER BY", "WHERE l.policy NOT LIKE '%shuffled%' ORDER BY"))
    filtered = dict(results, inventory_roi=([d[0] for d in cur.description], cur.fetchall()))
    copy = sqlite3.connect(":memory:")
    conn.backup(copy)
    copy.execute("INSERT INTO dispatch SELECT 'Staple with shuffled zones (control)', calls, served, rolled, fix_rate, "
                 "fixes_per_call, parts_missing, drive_per_call, moved, moved_fix_rate, cost_per_call, backorder_missing, wait_per_call, "
                 "line_fill, stock_value, part_jobs "
                 "FROM dispatch LIMIT 1")
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

    off_by_one = changed(results, "inventory_roi", load.EQUAL_SPACE, "cost_a_truck_day", lambda v: v + 1)
    wrong_page = report.build_report(off_by_one, week, stamp_text, day)
    require(figures_missing(wrong_page, results) == ["the balanced truck held to today's space"],
            "control: a report whose equal-space cost is a dollar off was not caught, or the wrong card was blamed")
    odds = "Staple with shuffled-history parts odds (control)"
    turned = changed(results, "controls", odds, "verdict", lambda v: "falls back")
    try:
        report.build_report(turned, week, stamp_text, day)
        stopped = False
    except ValueError:
        stopped = True
    print(f"  control: a report with the equal-space cost a dollar off is refused, and a build whose query says the "
          f"parts-odds control falls back {'stops' if stopped else 'goes on'}")
    require(stopped, "control: the report was built though its words contradict the controls query")


SECOND_SUBJECT = ("the", "a", "an", "my", "no", "nothing", "everything")    # words that open a new subject after "My dryer"
NOW_CLAIMS = ("today" + "'s rules", "the way a booking " + "screen")       # split, so this file does not hold them itself


def said_twice(sentence):
    """True when a sentence says one word twice in a row, as "My oven oven won't heat up" does."""
    words = re.findall(r"[a-z']+", sentence.lower())
    return any(a == b for a, b in zip(words, words[1:]))


def second_subjects(parts_list, vague):
    """Phrasings that open a second subject after the opener, as "the drum won't spin" does after "My dryer"."""
    return sorted(f"{appliance}: {p}" for appliance, (_, _, problems) in parts_list.items()
                  for p in [p for _, said, _ in problems.values() for p in said] + list(vague)
                  if p.lower().split()[0] in SECOND_SUBJECT)


def sure_of_vague(answers, vague, line):
    """Phrases the world files as saying nothing that Claude reads with confidence. A phrase read that surely says
    something, so it was filed wrongly."""
    return sorted(f"{a}: {words}" for a, table in answers.items() for words, x in table.items()
                  if any(v in words for v in vague) and x["confidence"] >= line)


def bands_recounted(jobs, answers, edges):
    """The confidence bands counted again from the bookings: {lower edge: (phrasings, bookings, share matching the
    booked code, share matching the true problem)}."""
    found = {}
    for j in jobs:
        x = answers[j["type"]][j["words"]]
        b = found.setdefault(max(edge for edge in edges if x["confidence"] >= edge), [set(), 0, 0, 0])
        b[0].add((j["type"], j["words"]))
        b[1] += 1
        b[2] += x["code"] == j["problem"].split(".")[1]
        b[3] += x["code"] == j["true"].split(".")[1]
    return {low: (len(said), n, booked / n, true / n) for low, (said, n, booked, true) in found.items()}


def overstated(bands):
    """The bands in which the reading is right less often than the confidence it stated, surest first."""
    return [low for low, (_, _, right, _) in sorted(bands.items(), reverse=True) if right < low]


def card_words(page, name):
    """One evidence card's words as a reader sees them, with no markup and no hover hints."""
    import html
    start = page.index(f'id="proof-{name}"')
    return html.unescape(re.sub(r"<[^>]+>", "", page[start:page.index("</section>", start)]))


def ai_card_faults(page, results, said):
    """What the report's AI card fails to say: the count of distinct phrasings, that the score after the question is
    an upper bound, and each confidence band with how often it was right."""
    from staple import report, triage
    ai = {r[0]: dict(zip(results["ai_step"][0], r)) for r in results["ai_step"][1]}
    words, whole = card_words(page, "ai_step"), card_text(page, "ai_step")
    faults = [] if f"in only {said} ways" in words else [f"the count of {said} distinct phrasings"]
    if f"{report.pct(ai[triage.ASKED]['coverage_pct'])}, an upper bound" not in words:
        faults.append("the score after the question called an upper bound")
    for band in report.rows(results, "ai_calibration"):
        if f"stated {band['stated_confidence']}" not in whole or f"right {report.pct(band['agrees_booked_pct'])}" not in whole:
            faults.append(f"the band {band['stated_confidence']} with how often it was right")
    return faults


def matcher_claims(texts):
    """Entries of {name: text} that present the keyword matcher as the way bookings are coded now."""
    plain = lambda text: text.lower().replace("\u2019", "'").replace("&#x27;", "'").replace("&#39;", "'")
    return sorted(f"{name}: '{claim}'" for name, text in texts.items() for claim in NOW_CLAIMS if claim in plain(text))


def plan_feed_faults(source):
    """How run.py could hand the week's plan other answers than the ones it scored: answers loaded more than once,
    or a plan built from anything but the two names the one load filled."""
    tree = ast.parse(source)

    def calls(owner, name):
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == name and getattr(n.func.value, "id", None) == owner]

    loads, builds = calls("triage", "load"), calls("plan", "build")
    filled = [[getattr(t, "id", None) for t in a.targets[0].elts] for a in ast.walk(tree)
              if isinstance(a, ast.Assign) and any(a.value is c for c in loads) and isinstance(a.targets[0], ast.Tuple)]
    faults = [] if len(loads) == 1 else [f"the answers are loaded {len(loads)} times"]
    fed = [[getattr(a, "id", None) for a in b.args[1:]] for b in builds]
    if len(filled) != 1 or fed != filled:
        faults.append("the week's plan is not built from the answers the run loaded")
    return faults


def stand_in_reader(body):
    """The stand-in's own answers to one request, nothing like the saved ones: every description gets the last
    problem code on the list, unsure when it says "acting up", and every question offers the first two codes."""
    import json
    from staple import triage
    text = body["messages"][0]["content"]
    codes = re.findall(r"^([A-Z0-9_]+): ", text, re.M)
    said = re.findall(r"^(\d+)\. (.*)$", text, re.M)
    if body["system"] == triage.FOLLOWUP_SYSTEM:
        reply = [{"i": int(i), "question": "Which of these is it?",
                  "answers": [{"text": c.lower(), "code": c} for c in codes[:2]]} for i, _ in said]
    else:
        reply = [{"i": int(i), "code": codes[-1], "confidence": 0.3 if "acting up" in words else 0.95} for i, words in said]
    return {"content": [{"type": "text", "text": json.dumps(reply)}], "stop_reason": "end_turn"}


def not_carried(week, answers):
    """Calls in a week's plan whose first reading is not the one these answers hold for the customer's words."""
    return [j["job"] for j in week["jobs"]
            if (j["ai"]["first"], j["ai"]["first_confidence"]) != (answers[j["type"]][j["words"]]["code"],
                                                                   answers[j["type"]][j["words"]]["confidence"])]


@check("triage-honest")
def triage_honest():
    """The AI step says what it is: clean customer sentences, a stated sample, an upper bound called one, a
    confidence held to what it states, and a live run that reaches the plan or stops in plain words."""
    import random
    from staple import backtest, metrics, plan, report, triage, world
    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
    answers, followups = triage.load(conn)
    jobs = backtest.test_jobs(conn)

    # The customer's sentences.
    sentences = [words for _, words in conn.execute("SELECT DISTINCT type, words FROM jobs")]
    twice, second = [s for s in sentences if said_twice(s)], second_subjects(world.CATALOG, world.VAGUE)
    print(f"  {len(sentences)} distinct customer sentences: {len(twice)} say a word twice in a row, and {len(second)} "
          f"phrasings open a second subject")
    require(not twice and not second, f"a customer sentence does not read as one clause: {(twice + second)[:3]}")
    old = {"range": (["My oven"], [], {"OVEN_NO_HEAT": ("Oven not heating", ["oven won't heat up"], {}),
                                       "NO_POWER": ("No power", ["nothing comes on"], {})}),
           "dryer": (["My dryer"], [], {"NO_TUMBLE": ("Drum not turning", ["the drum won't spin"], {})}),
           "furnace": (["My furnace"], [], {"BLOWER_ONLY": ("Blower problem", ["no air coming out of the vents"], {})})}
    require(said_twice("My oven oven won't heat up") and said_twice("My fridge fridge section is getting warm")
            and len(second_subjects(old, [])) == 3, "control: the old phrasings pass the sentence rules")
    print("  control: the old sentences are refused, two for a word said twice and three for a second subject")

    line = triage.BLEND_BELOW
    vague = [(a, w, x) for a, table in answers.items() for w, x in table.items() if any(v in w for v in world.VAGUE)]
    sure = sure_of_vague(answers, world.VAGUE, line)
    print(f"  {len(vague)} sentences use a phrase filed as saying nothing: Claude's confidence in them runs from "
          f"{min(x['confidence'] for _, _, x in vague)} to {max(x['confidence'] for _, _, x in vague)}, against a line of {line}")
    require(vague and not sure, f"a phrase filed as vague is read with confidence, so it says something: {sure[:3]}")
    a, words, x = vague[0]
    planted = dict(answers, **{a: dict(answers[a], **{words: dict(x, confidence=0.8)})})
    require(sure_of_vague(planted, world.VAGUE, line) == [f"{a}: {words}"], "control: a vague phrase read at 0.8 was not caught")
    print("  control: one of them read at 0.8, as the phrase about a strange noise was, is refused")

    # The page: the sample, the upper bound and the confidence bands.
    said = len({(j["type"], j["words"]) for j in jobs})
    week, _ = shared_week()
    stamp_text, day, _, _ = report.stamp()
    page = report.build_report(results, week, stamp_text, day)
    faults = ai_card_faults(page, results, said)
    print(f"  the report's AI card: {said} distinct phrasings behind {len(jobs):,} bookings, the score after the question "
          f"called an upper bound, {len(results['ai_calibration'][1])} confidence bands; missing: {faults or 'nothing'}")
    require(not faults, f"the report's AI card does not carry: {faults}")
    require("upper bound" in triage.ASKED and "control" in triage.RANDOM_REPLIES,
            "the rows for the follow-up question no longer say which is the upper bound and which the control")
    top = results["ai_calibration"][1][0][0]
    wrong = {"a count of phrasings one too high": report.build_report(
                 changed(results, "ai_step", "Claude", "phrasings", lambda v: v + 1), week, stamp_text, day),
             "the upper bound left unsaid": page.replace(", an upper bound", "", 1),
             "a band's share changed": report.build_report(
                 changed(results, "ai_calibration", top, "agrees_booked_pct", lambda v: v - 10), week, stamp_text, day)}
    caught = {name: bool(ai_card_faults(p, results, said)) for name, p in wrong.items()}
    print(f"  control: {sum(caught.values())} of {len(caught)} wrong AI cards refused")
    require(all(caught.values()), f"control: a wrong AI card passed: {[n for n, c in caught.items() if not c]}")

    # The confidence figure, counted again from the bookings and held to what Claude states.
    table = {low: (n_said, n, booked, true) for low, n_said, n, booked, true in
             conn.execute("SELECT low, phrasings, jobs, agree_booked, agree_true FROM triage_calibration")}
    bands = bands_recounted(jobs, answers, triage.BANDS)
    require(set(table) == set(bands) and all(table[low][:2] == bands[low][:2] and abs(table[low][2] - bands[low][2]) < 1e-9
                                             and abs(table[low][3] - bands[low][3]) < 1e-9 for low in bands),
            "the confidence table does not match a recount from the bookings")
    require(sum(b[1] for b in bands.values()) == len(jobs) and sum(b[0] for b in bands.values()) == said,
            "the confidence bands do not hold every booking and every phrasing once")
    lowest = min(bands)
    upper = {low: min([edge for edge in triage.BANDS if edge > low], default=1.0) for low in bands}
    ceiling = upper[lowest]
    for low in sorted(bands, reverse=True):
        print(f"  stated {low:.2f} to {upper[low]:.2f}: {bands[low][1]:,} bookings in {bands[low][0]} phrasings, "
              f"right {bands[low][2]:.1%}")
    require(not overstated(bands), f"Claude is right less often than it states in the bands from {overstated(bands)}")
    require(bands[lowest][2] < ceiling, "Claude is right in its least sure band as often as a sure reading, so its doubt tells nothing")
    rng, dealt = random.Random(7), {}
    for a, readings in answers.items():
        values = list(readings.values())
        rng.shuffle(values)
        dealt[a] = dict(zip(readings, values))
    broken = bands_recounted(jobs, dealt, triage.BANDS)
    print(f"  control: with the answers shuffled between sentences, the surest band is right {broken[max(broken)][2]:.1%} "
          f"and {len(overstated(broken))} of {len(broken)} bands overstate")
    require(max(broken) in overstated(broken), "control: shuffled answers still pass the confidence test")

    # No public words present the keyword matcher as how bookings are coded now.
    texts = {}
    for p in public_files():
        if report.PUBLISHED not in p.parents:    # the published copy is held to a fresh build by the report check
            try:
                texts[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                pass
    texts.update({f"a fresh {report.FILES[kind]}": text for kind, text in report.build(results, week, stamp_text, day).items()})
    claims = matcher_claims(texts)
    print(f"  {len(texts)} public files and fresh pages: {len(claims)} present the keyword matcher as how bookings are coded now")
    require(not claims, f"the keyword matcher is presented as how bookings are coded now: {claims[:3]}")
    old_words = {"the old code note": NOW_CLAIMS[0].capitalize() + ": the code whose label shares the most words",
                 "the old manual": f"It is compared with keyword rules, {NOW_CLAIMS[1]} matches words"}
    require(len(matcher_claims(old_words)) == 2, "control: the old sentences about the matcher pass")
    print("  control: the two old sentences that did are refused")

    # A live run: its answers reach the week's plan, and each way it can fail stops in one plain sentence.
    with stand_in_api(stand_in_reader) as (url, seen):
        live, questions = triage.ask_live(conn, "test-key", url)
    differ = sum(live[a][w] != answers[a][w] for a in live for w in live[a])
    live_week = plan.build(conn, live, questions)
    asked = sum(d["ai"]["asked"] for d in live_week["days"])
    lost = not_carried(live_week, live)
    print(f"  a live run against a local stand-in: {seen['requests']} requests, {differ} of {sum(len(t) for t in live.values())} "
          f"answers unlike the saved ones; of the plan's {len(live_week['jobs']):,} calls, {len(lost)} run on another reading, "
          f"and {asked} got the stand-in's question")
    require(differ and not lost and asked, "the week's plan does not run on the answers a live run brought back")
    require(not_carried(week, live), "control: a plan built from the saved answers passes as built from the live ones")
    try:
        plan.build(conn)
        loads_own = True
    except TypeError:
        loads_own = False
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    feed = plan_feed_faults(source)
    require(not loads_own and not feed, f"the plan can still run on answers the run did not score: {feed or 'it loads its own'}")
    twice_loaded = source.replace("plan.build(conn, answers, followups)", "plan.build(conn, *triage.load(conn))")
    require(twice_loaded != source and len(plan_feed_faults(twice_loaded)) == 2,
            "control: a run that loads the saved answers again for the plan passes")
    print("  control: a plan built from the saved answers, and a run that loads them a second time, are refused; "
          "the plan no longer loads answers of its own")

    def stop(reply, status=200, down=False):
        """The sentence a live run stops on when the API answers with reply, or None when it goes through."""
        with stand_in_api(reply, status) as (url, _):
            if not down:
                try:
                    triage.ask_live(conn, "test-key", url)
                except SystemExit as halt:
                    return str(halt.code)
                return None
        try:    # the stand-in is gone by now, so nothing answers at its address
            triage.ask_live(conn, "test-key", url)
        except SystemExit as halt:
            return str(halt.code)
        return None

    text = lambda said, why="end_turn": {"content": [{"type": "text", "text": said}], "stop_reason": why}
    stops = {"a rejected key": (stop({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, 401),
                                "ANTHROPIC_API_KEY"),
             "a reply that is not JSON": (stop("<html>Bad gateway</html>"), "not JSON"),
             "a malformed answer": (stop(text("[]")), "agreed form"),
             "a reply cut short": (stop(text("[", "max_tokens")), "cut off"),
             "an API that cannot be reached": (stop(text("[]"), down=True), "could not be reached")}
    for name, (halt, _) in stops.items():
        print(f"  {name}: {halt}")
    plain = {name: bool(halt) and halt.startswith("--live stopped: ") and "\n" not in halt and len(halt) < 300 and needle in halt
             for name, (halt, needle) in stops.items()}
    require(all(plain.values()), f"a failed live run does not stop on one plain sentence naming its cause: "
                                 f"{[n for n, ok in plain.items() if not ok]}")
    key = os.environ.pop("ANTHROPIC_API_KEY", None)    # taken out, never read: the run below must stop before any request
    try:
        triage.load(conn, live=True)
        keyless = None
    except SystemExit as halt:
        keyless = str(halt.code)
    finally:
        if key is not None:
            os.environ["ANTHROPIC_API_KEY"] = key
    print(f"  with no key set: {keyless}")
    require(keyless and "ANTHROPIC_API_KEY" in keyless, "a live run with no key does not say which key it needs")
    require(stop(stand_in_reader) is None, "control: a live run with good replies also stops")
    print(f"  control: {len(stops)} of {len(stops)} failures stop on one sentence, and good replies go through")


ALLOWED_NUMERALS = ("SHA-256", "per 100 jobs")    # a name and a unit, neither a figure the code holds
# Forms an American reader would not write, split so this file does not hold them itself.
ABROAD = [a + b for a, b in (("col", "our"), ("cent", "re"), ("per ", "cent"), ("catalog", "ue"), ("arte", "fact"), ("lic", "ence"),
                             ("behav", "iour"), ("fav", "our"), ("neighb", "our"), ("organ", "is"), ("recogn", "is"),
                             ("analy", "se"), ("optim", "is"), ("priorit", "is"), ("summar", "is"), ("travel", "l"),
                             ("model", "l"), ("label", "l"), ("cancel", "led"), ("program", "me"), ("kilo", "met"),
                             ("lit", "re"), ("judg", "ement"), ("whil", "st"), ("among", "st"), ("dear", "er"), ("cost", "ed"),
                             ("gr", "ey"))]
OLD_NAMES = {"first-" + "visit fix": "first-time fix", "first-" + "visit chance": "first-time fix chance",
             "second " + "visit": "repeat visit", "second truck " + "roll": "repeat visit", "the current " + "way": "the current rules",
             "short" + "cut": "corner", "methods " + "manual": "methods page", "Methods and " + "logic": "Methods page", "Instruction " + "Manual": "Instruction manual"}
FALSE_EVERY = ("Every number on the report comes " + "from", "every constant in these " + "manuals", "Every control here falls " + "back")
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
COUNTED = "calls|trucks|parts|jobs|visits|technicians|bookings|days|moves|items|zones|routes|answers|minutes"


def typed_numerals(template):
    """Numerals typed into a page template. A formula, a code name and a placeholder are not typed figures."""
    text = re.sub(r"<(code|pre|var)\b[^>]*>.*?</\1>", " ", template, flags=re.S)
    text = re.sub(r"\{\{\w+\}\}|<[^>]+>|&#?\w+;", " ", text)
    for allowed in ALLOWED_NUMERALS:
        text = text.replace(allowed, " ")
    return re.findall(r"[^\s(]*\d[^\s),;]*", text)


def abroad(texts):
    """Entries of {name: text} that hold a British form, a metric distance or a day-first date."""
    hits = []
    for name, text in texts.items():
        found = sorted(set(m.group(0).lower() for m in re.finditer(r"\b(?:" + "|".join(ABROAD) + r")\w*|\bkm\b", text, re.I)))
        found += sorted(set(re.findall(rf"\b\d{{1,2}} (?:{MONTHS})\b", text)))
        hits += [f"{name}: {word}" for word in found]
    return hits


def old_names(texts):
    """Entries of {name: text} that call a thing by a name the pages have dropped."""
    return sorted(f"{name}: '{old}' for {new}" for name, text in texts.items() for old, new in OLD_NAMES.items() if old in text)


def page_words(page):
    """A page as a reader meets it: its words and its hover hints, with the markup out."""
    import html
    hints = " ".join(re.findall(r'data-tip="([^"]*)"', page))
    return html.unescape(re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", " ", page, flags=re.S) + " " + hints)


def figure_of(cell):
    """The number in one table cell and the decimal places it shows, or None for a cell that holds no number."""
    m = re.search(r"\d[\d,]*(?:\.(\d+))?", cell)
    if not m:
        return None
    return float(m.group(0).replace(",", "")), len(m.group(1) or "")


def comparisons(page):
    """The tables that set two figures beside their difference: one for each day of the week."""
    return [t for t in re.findall(r'<table class="cmp">.*?</table>', page, flags=re.S) if ">Difference</th>" in t]


def comparison_faults(page):
    """Rows of the day's comparison whose difference is not the difference of the two figures beside it, whose
    three figures are not shown at one precision, or whose tie is shown as a number and not in words."""
    faults = []
    for table in comparisons(page):
        for row in re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>")[1], flags=re.S):
            name, ours, theirs, diff = [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
            (x, px), (y, py) = figure_of(ours), figure_of(theirs)
            d, pd = (0.0, px) if diff == "no difference" else figure_of(diff)
            signed = -d if diff.startswith("−") else d
            if not px == py == pd:
                faults.append(f"{name}: {ours}, {theirs} and {diff} are not at one precision")
            elif abs(round(x - y, px) - signed) > 1e-9:
                faults.append(f"{name}: {ours} less {theirs} is not {diff}")
            elif not d and diff != "no difference":
                faults.append(f"{name}: a tie is shown as {diff}")
    return faults


def column_faults(page):
    """Columns of the tables under the charts whose numbers are shown at more than one precision."""
    faults = []
    for figure in re.findall(r"<figure>.*?</figure>", page, flags=re.S):
        title = re.sub(r"<[^>]+>", "", re.search(r'<span class="fig-title"[^>]*>(.*?)</span>', figure, flags=re.S).group(1))
        heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<th[^>]*>(.*?)</th>", figure, flags=re.S)]
        rows = [[re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)]
                for r in re.findall(r"<tr>(.*?)</tr>", figure.split("<tbody>")[1], flags=re.S)]
        for i, head in enumerate(heads):
            shown = {len(c.split(".")[1]) if "." in c else 0 for c in (r[i] for r in rows) if re.fullmatch(r"-?\d+(\.\d+)?", c)}
            if len(shown) > 1:
                faults.append(f"{title}, {head}: {sorted(shown)} decimal places in one column")
    return faults


def bare_part_names(page, parts_list):
    """Uses of a part name that more than one appliance has, with no appliance in front of it. Names are matched
    whole and longest first, so "oven door gasket" is not read as a bare "door gasket"."""
    from staple import world
    names = sorted({n for _, parts, _ in parts_list.values() for n, *_ in parts}, key=len, reverse=True)
    words, bare = page_words(page), []
    for m in re.finditer("|".join(map(re.escape, names)), words):
        before = words[max(0, m.start() - 16):m.start()]
        if m.group(0) in world.SHARED_NAMES and not any(before.endswith(a + " ") or before.endswith(a + "/") for a in parts_list):
            bare.append(f"{m.group(0)} after '{before[-12:].strip()}'")
    return bare


def copy_faults(texts):
    """What reader-facing words get wrong beyond spelling and names: a count of one with a plural, a cost a call
    or a truck-day shown short of the cent, and a sentence that claims every figure or every control."""
    faults = []
    for name, text in texts.items():
        faults += [f"{name}: '{m.group(0)}'" for m in re.finditer(rf"(?<![\d.,$])\b1 (?:{COUNTED})\b", text)]
        faults += [f"{name}: '{m.group(0)}'" for m in re.finditer(r"\$[\d,]+(?:\.\d)?(?![\d.])\s+a\s+(?:call|truck-day)\b", text)]
        faults += [f"{name}: '{claim}'" for claim in FALSE_EVERY if claim in text]
    return faults


@check("wording")
def wording():
    """The words match the code: no numeral typed into a template, American forms, one name for each thing,
    differences that match the figures beside them, one precision in a column, and part names with their appliance."""
    from staple import metrics, report, world
    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
    week, _ = shared_week()
    stamp_text, day, _, _ = report.stamp()
    pages = report.build(results, week, stamp_text, day)
    page = pages["report"]

    # No numeral is typed into a template: every figure on the manual and the methods page comes from the code.
    templates = {p.name: p.read_text(encoding="utf-8") for p in sorted(report.PAGES.glob("*.html"))}
    typed = {name: typed_numerals(text) for name, text in templates.items()}
    filled = sum(len(re.findall(r"\{\{\w+\}\}", text)) for text in templates.values())
    print(f"  {len(templates)} page templates: {filled} values read from the code, {sum(map(len, typed.values()))} numerals typed in")
    require(not any(typed.values()), f"a numeral is typed into a template: { {n: t[:4] for n, t in typed.items() if t} }")
    require(typed_numerals("<p>Level weights 25, 45 and 30, and a <code>P = 0</code> run with {{seed}}.</p>") == ["25", "45", "30"],
            "control: three numerals typed into a sentence were not all caught, or a formula was")
    print("  control: three numerals typed into a sentence are refused, and a formula and a placeholder are not")

    # American forms, in every public file but the published copy, and in the pages as built.
    texts = {}
    for p in public_files():
        if report.PUBLISHED not in p.parents:    # the published copy is held to a fresh build by the report check
            try:
                texts[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                pass
    built = {f"a fresh {report.FILES[kind]}": page_words(text) for kind, text in pages.items()}
    foreign = abroad({**texts, **built})
    print(f"  {len(texts)} public files and {len(built)} fresh pages: {len(foreign)} British forms, metric distances or day-first dates")
    require(not foreign, f"a public file is not in American form: {foreign[:5]}")
    planted = abroad({"planted": ABROAD[1].capitalize() + "d on a " + ABROAD[0] + ", 15 k" + "m away, on 28 " + "Oct."})
    require(len(planted) == 4, f"control: a sentence with four foreign forms gave {planted}")
    print("  control: a sentence with two British words, a metric distance and a day-first date is refused four times")

    # One name for each thing, and no sentence that claims every figure or every control, in the words a reader meets.
    copy = {**built, **{name: text for name, text in texts.items() if name.endswith(".md")}}
    renamed, wrong = old_names(copy), copy_faults(copy)
    rates = sum(len(re.findall(r"\$[\d,]+\.\d\d\s+a\s+(?:call|truck-day)\b", text)) for text in copy.values())
    print(f"  {len(copy)} pages and notes: {len(renamed)} dropped names, {len(wrong)} plural slips, rates short of the cent "
          f"or false 'every' sentences; {rates:,} costs a call or a truck-day are shown to the cent")
    require(rates, "no cost a call or a truck-day was found on the pages, so the rule for one was not exercised")
    require(not renamed, f"a thing is called by a dropped name: {renamed[:4]}")
    require(not wrong, f"the copy has a plural slip, a rate short of the cent or a false 'every' sentence: {wrong[:4]}")
    slip = {"planted": f"1 calls tomorrow, {next(iter(OLD_NAMES))} of 85%. {FALSE_EVERY[0]} one of them. It took 21 calls, "
                       "at $122 a call and $104.3 a truck-day against $121.87 a call."}
    require(len(old_names(slip)) == 1 and len(copy_faults(slip)) == 4, "control: a planted dropped name, plural slip, short rate or 'every' sentence passed")
    print("  control: a dropped name, '1 calls', '$122 a call', '$104.3 a truck-day' and a false 'every' sentence are refused, "
          "and '21 calls' and '$121.87 a call' are not")

    # The day's comparison adds up on the page, and each column under a chart reads at one precision.
    rows = sum(t.count("<tr>") - 1 for t in comparisons(page))
    adds, columns = comparison_faults(page), column_faults(page)
    print(f"  {rows} rows of the day's comparison: {len(adds)} whose difference is not that of the figures beside it; "
          f"{page.count('<figure>')} tables under charts: {len(columns)} columns at mixed precision")
    require(rows and not adds, f"a comparison does not add up as shown: {adds[:3]}")
    require(not columns, f"a table column mixes precisions: {columns[:3]}")
    first = comparisons(page)[0]
    cell = re.findall(r"<td[^>]*>(.*?)</td>", first, flags=re.S)[3]
    bent = page.replace(first, first.replace(f"<td>{cell}</td>", f"<td>{cell[0]}99.9 points</td>", 1), 1)
    require(bent != page and comparison_faults(bent), "control: a difference changed on the page passed")
    hidden = page.replace(first, first.replace(f"<td>{cell}</td>", "<td>no difference</td>", 1), 1)
    require(hidden != page and comparison_faults(hidden), "control: a real difference written as no difference passed")
    tie = next((t for t in comparisons(page) if "<td>no difference</td>" in t), None)
    require(tie, "no day of this week has a tie, so the rule for one was not exercised")
    require(comparison_faults(page.replace(tie, tie.replace("<td>no difference</td>", "<td>+0</td>", 1), 1)),
            "control: a tie shown as +0 passed")
    holder = next(f for f in re.findall(r"<figure>.*?</figure>", page, flags=re.S) if re.search(r"<td>\d+\.\d0</td>", f))
    money = re.search(r"<td>(\d+\.\d)0</td>", holder)
    short = page.replace(holder, holder.replace(money.group(0), f"<td>{money.group(1)}</td>", 1), 1)
    require(short != page and column_faults(short), "control: a cell shown at another precision than its column passed")
    print("  control: a difference changed on the page, a real one written as no difference, a tie shown as +0, "
          "and a cell with its last zero dropped, are refused")

    # A part name more than one appliance has carries its appliance.
    bare = bare_part_names(page, world.CATALOG)
    shown = sum(len(re.findall(re.escape(n), page_words(page))) for n in world.SHARED_NAMES)
    print(f"  {len(world.SHARED_NAMES)} part names belong to more than one appliance; of {shown:,} uses on the report, "
          f"{len(bare)} come without the appliance")
    require(shown and not bare, f"a shared part name is shown without its appliance: {bare[:3]}")
    name = next(n for n in sorted(world.SHARED_NAMES) if f"dryer {n}" in page)
    require(bare_part_names(page.replace(f"dryer {name}", name, 1), world.CATALOG),
            "control: a shared part name with its appliance taken off passed")
    print(f"  control: '{name}' with its appliance taken off once is refused")

    # The back-order forecast the card quotes is the sum of the rows the query marks for the back-test.
    cols, data = results["backorder_exposure"]
    scored = [dict(zip(cols, r)) for r in data if r[cols.index("back_test")]]
    calls, stuck = sum(r["expected_calls"] for r in scored), sum(r["expected_stuck"] for r in scored)
    quoted = f"{calls:.1f} calls and {stuck:.1f} stuck"
    print(f"  the back-order card quotes {quoted}, the sums over the {len(scored)} rows scored")
    require(quoted in card_words(page, "backorder_exposure"), "the back-order card does not quote the forecast its rows add up to")


def unnamed(found):
    """Corners whose comment does not say what would lift them."""
    return [f"{name}: {words[:60]}" for name, words in found if "upgrade" not in words.lower()]


def of_method(corner):
    """Whether a corner is cut in the method itself: in the engine, not in the checks or the file that draws the pages."""
    return corner[0].startswith("staple/") and corner[0] != "staple/report.py"


def corner_faults(method_page, report_page, found, marker):
    """Where the pages disagree with the corners the code marks: one missing, listed twice or miscounted, or the
    marker written anywhere but the one sentence that defines it."""
    import html
    faults = []
    section = re.search(r'<section id="corners">.*?</section>', method_page, flags=re.S).group(0)
    listed = [(html.unescape(n), html.unescape(w)) for n, w in re.findall(r"<li><code>(.*?)</code>: (.*?)</li>", section, flags=re.S)]
    if sorted(listed) != sorted(found):
        faults.append(f"the methods page lists {len(listed)} corners where the code marks {len(found)}")
    if page_words(method_page).count(marker) != 1:
        faults.append(f"the methods page writes the marker {page_words(method_page).count(marker)} times, not once")
    in_method = sum(map(of_method, found))
    said = re.search(r"The method cuts (\d+) corners on purpose", page_words(report_page))
    if not said or int(said.group(1)) != in_method:
        faults.append(f"the report states {said.group(1) if said else 'no count'} where the method cuts {in_method}")
    counts = re.search(r"(\d+) in the method, and (\d+) in the checks and the build", page_words(section))
    if not counts or [int(n) for n in counts.groups()] != [in_method, len(found) - in_method]:
        faults.append("the methods page does not state the two counts of its own list")
    return faults


@check("corners")
def corners_listed():
    """The corners cut on purpose are read from the comments that mark them, each counted once and each naming
    its upgrade, and the pages state the same count."""
    from staple import metrics, report
    marker, found, sources = report.MARKER, report.corners(), report.corner_files()
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in sources]
    opens = sum(line.strip().startswith(marker) for text in texts for line in text.splitlines())
    written = sum(text.count(marker) for text in texts)
    print(f"  {len(found)} corners marked in {len({name for name, _ in found})} of {len(sources)} source files; the marker is written "
          f"{written} times, {written - len(found)} of them inside a string, which is not a corner")
    require(found and len(found) == opens, f"{len(found)} corners were found where {opens} lines open with the marker")
    require(written > len(found), "no string holds the marker any more, so the rule against counting one was not exercised")
    require(not unnamed(found), f"a corner does not name its upgrade: {unnamed(found)[:3]}")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        py, sh = Path(tmp) / "planted.py", Path(tmp) / "planted.sh"
        py.write_text(f'said = "{marker} inside a string"\n{marker} a real one. The upgrade\n# runs on to this line.\nvalue = 1\n'
                      "# and this note is about something else\n", encoding="utf-8")
        sh.write_text(f'echo "{marker} inside a string"\n{marker} a real one, with no way out named.\nexit 0\n', encoding="utf-8")
        planted = report.corners([py, sh])
    require(planted == [("planted.py", "a real one. The upgrade runs on to this line."),
                        ("planted.sh", "a real one, with no way out named.")],
            f"control: two planted files, each with one corner and the marker in a string, gave {planted}")
    require(len(unnamed(planted)) == 1, "control: a corner that names no upgrade passed")
    print("  control: the marker inside a string is not counted in Python or in a shell script, a comment that runs on is read "
          "whole, and a corner that names no upgrade is refused")

    conn = shared_world()
    results_written(conn)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
    week, _ = shared_week()
    stamp_text, day, _, _ = report.stamp()
    pages = report.build(results, week, stamp_text, day)
    method, front = pages["method"], pages["report"]
    faults = corner_faults(method, front, found, marker)
    in_method = sum(map(of_method, found))
    print(f"  the methods page lists {len(found)} corners once each, {in_method} in the method and {len(found) - in_method} in the "
          f"checks and the build, and defines the marker in one place; the report states {in_method}: {len(faults)} faults")
    require(not faults, f"the pages and the code disagree on the corners: {faults}")
    for name in ("staple/predict.py", "staple/world.py", "staple/dispatch.py"):
        require(any(n == name for n, _ in found), f"the report names a corner in {name} that no comment there marks")
    one = re.search(r"<li><code>.*?</li>", method[method.index('<section id="corners">'):], flags=re.S).group(0)
    said = re.search(r"The method cuts \d+ corners on purpose", front).group(0)
    def caught(page, front_page, word):
        return any(word in fault for fault in corner_faults(page, front_page, found, marker))
    require(caught(method.replace(one, "", 1), front, "lists"), "control: a methods page with one corner dropped passed")
    require(caught(method.replace(one, one + one, 1), front, "lists"), "control: a methods page listing a corner twice passed")
    require(caught(method, front.replace(said, f"The method cuts {in_method + 1} corners on purpose", 1), "report states"),
            "control: a report stating one corner too many passed")
    require(caught(method.replace('<section id="corners">', f'<p>{marker}</p><section id="corners">', 1), front, "marker"),
            "control: a methods page writing the marker a second time passed")
    print("  control: a corner dropped from the page, one listed twice, a wrong count on the report and the marker written "
          "twice are each refused")


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
    if not day or not (re.fullmatch(rf"(?:{MONTHS}) [1-9]\d?, \d{{4}}", day.group(1))
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
        code = commit("staple/x.py", "2026-10-02T23:30:00-04:00")    # already Oct 3 in UTC: the day is the change's own
        expected = f"Build 2 · Oct 2, 2026 · {code[:7]}"
        text, day, pattern, warned = report.stamp(repo)
        print(f"  a scratch history of two changes is stamped \"{text}\", results last changed {day}")
        require(text == expected and day == "Oct 2, 2026" and not warned, f"expected \"{expected}\" and Oct 2, 2026 with no warning")
        require(pattern == report.STAMP_RE and re.fullmatch(report.STAMP_RE, text), "the stamp does not fit its own pattern")

        commit("docs/index.html", "2026-10-05T09:00:00-04:00")
        require(report.stamp(repo)[0] == expected, "committing only the published copy moved the stamp")
        print("  committing only the published copy leaves the stamp as it was")
        third = commit("check.py", "2026-10-06T09:00:00-04:00")
        moved = report.stamp(repo)
        require(moved[0] == f"Build 4 · Oct 6, 2026 · {third[:7]}" and moved[1] == "Oct 2, 2026",
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
        require(cut[0] == f"Oct 6, 2026 · {third[:7]}+" and cut[3] and re.fullmatch(report.STAMP_RE, cut[0]),
                f"a copy holding part of its history should carry no build number and say why, got {cut[0]!r}")
        print(f"  a copy holding part of its history is stamped \"{cut[0]}\" and warns")

        none = report.stamp(bare)
        require(none[0] == report.NO_HISTORY and none[2] is None and none[3], f"a folder with no history should say so, got {none[0]!r}")
        require(not re.fullmatch(report.STAMP_RE, report.NO_HISTORY), "control: the no-history words fit the stamp pattern")
        print(f"  a folder with no history says \"{none[0]}\" and warns; those words do not fit the stamp pattern")

    here = report.stamp()
    print(f"  this repository: \"{here[0]}\", results last changed {here[1]}" + "".join(f"; warning: {w}" for w in here[3]))
    require(here[0] == report.NO_HISTORY or re.fullmatch(report.STAMP_RE, here[0]), "this repository's stamp fits no known shape")

    one, _ = report.build_manuals("Build 1 · Sep 1, 2026 · abcdef1", "Sep 1, 2026")
    two, _ = report.build_manuals("Build 9 · Oct 9, 2026 · 1234567+", "Oct 9, 2026")
    require(one != two and without_stamp(one) == without_stamp(two),
            "two builds that differ only in their stamps do not compare equal once the stamps are emptied")
    said = one.replace("</main>", f"<p>{report.NO_HISTORY}</p><p>Build 1 · Sep 1, 2026 · abcdef1</p></main>", 1)
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
        if pattern:
            shown = re.search(r'(<span class="content-day"[^>]*>)([^<]*)</span>', page)
            month, date, year = shown.group(2).replace(",", "").split()
            for what, words in (("written day first", f"{date} {month} {year}"), ("left out", "")):
                planted = page.replace(shown.group(0), f"{shown.group(1)}{words}</span>", 1)
                require(page_faults(planted, pattern) == ["no day the results last changed"],
                        f"control: a page with its results day {what} passed")
            print(f"  control: a page whose results day is written day first, or left out, is refused; \"{shown.group(2)}\" passes")
        ours = next(r for r in results["dispatch_scorecard"][1] if r[0].startswith("Staple:"))
        figure = f"{ours[3]:.1f}%"
        require(figure in page, "the page does not show the dispatch scorecard's own first-time fix figure")
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


def columns_of(conn):
    """Every table's values by column, as {table.column: [values in row order]}."""
    out = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
        names = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {', '.join(str(i + 1) for i in range(len(names)))}").fetchall()
        out.update({f"{table}.{name}": [r[i] for r in rows] for i, name in enumerate(names)})
    return out


def wait_faults(conn):
    """Where the recorded waits break their own rules, as {rule: repairs that break it}."""
    from staple import world
    count = lambda sql: one(conn, sql)
    faults = {
        "lacked a part and has no dates": count(
            "SELECT COUNT(*) FROM jobs j LEFT JOIN waits w USING (job) WHERE j.parts_on_truck = 0 AND w.job IS NULL"),
        "had its parts and has dates": count(
            "SELECT COUNT(*) FROM jobs j JOIN waits w USING (job) WHERE j.parts_on_truck = 1"),
        "ordered before the visit or more than a day after": count(
            "SELECT COUNT(*) FROM jobs j JOIN waits w USING (job) "
            "WHERE julianday(w.order_day) - julianday(j.day) NOT IN (0, 1)"),
        "came back on or before the order day": count("SELECT COUNT(*) FROM waits WHERE repeat_visit_day <= order_day")}
    # Each waiting repair with the latest day a center could first ship one of its missing parts, where one was stuck.
    rows = conn.execute("""SELECT julianday(w.repeat_visit_day) - julianday(w.order_day),
                                  julianday(w.repeat_visit_day) - julianday(MAX(b.ships))
                           FROM waits w JOIN jobs j USING (job) JOIN job_parts jp USING (job)
                           JOIN trucks t ON t.tech = j.tech
                           LEFT JOIN truck_stock s ON s.truck = t.truck AND s.part = jp.part
                           LEFT JOIN backorders b ON b.part = jp.part AND b.starts <= w.order_day AND w.order_day < b.ships
                           WHERE s.part IS NULL GROUP BY j.job""").fetchall()
    faults["arrived after no made-up lead time"] = sum(1 for wait, after in rows if after is None and wait not in world.LEAD_TIMES)
    faults["arrived before a center could ship it"] = sum(1 for _, after in rows if after is not None and after < min(world.LEAD_TIMES))
    return {rule: n for rule, n in faults.items() if n}


@check("history-dates")
def history_dates():
    """The order day and the repeat-visit day of each repair that failed for a missing part are in the history,
    obey their rules, and moved no other row when they were added."""
    from staple import db, world
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn, bare = built_world(Path(tmp) / "with.db"), db.fresh(Path(tmp) / "without.db")
        world.build(bare, waits=False)
        ours, theirs = columns_of(conn), columns_of(bare)
        new = sorted(c for c in ours if c.split(".")[0] in ("waits", "lead_times"))
        shared = [c for c in ours if c not in new]
        moved = [c for c in shared if ours[c] != theirs.get(c)]
        tables = len({c.split(".")[0] for c in shared})
        print(f"  the world built with and without the two days: {len(shared)} other columns in {tables} tables, {len(moved)} "
              f"hold different values; the new columns are {', '.join(new)}")
        require(len(new) == 5 and not any(theirs[c] for c in new), f"the build without the days still wrote them: {new}")
        require(shared and not moved, f"adding the days moved other rows: {moved[:5]}")
        bare.execute("UPDATE jobs SET zone = zone + 1 WHERE job = 7")
        bent = columns_of(bare)
        require([c for c in shared if ours[c] != bent[c]] == ["jobs.zone"], "control: a build with one value changed compared equal")
        bare.close()
        print("  control: a build with one job's zone changed is refused, on that column alone")

        waiting = one(conn, "SELECT COUNT(*) FROM waits")
        lacked = one(conn, "SELECT COUNT(*) FROM jobs WHERE parts_on_truck = 0")
        faults = wait_faults(conn)
        print(f"  {lacked:,} repairs lacked a part and {waiting:,} carry an order day and a repeat-visit day; rules broken: {faults or 'none'}")
        require(waiting == lacked and not faults, f"the recorded waits break their rules: {faults}")
        held = conn.execute("""SELECT w.job, MAX(b.ships) FROM waits w JOIN jobs j USING (job) JOIN job_parts jp USING (job)
                               JOIN trucks t ON t.tech = j.tech
                               LEFT JOIN truck_stock s ON s.truck = t.truck AND s.part = jp.part
                               JOIN backorders b ON b.part = jp.part AND b.starts <= w.order_day AND w.order_day < b.ships
                               WHERE s.part IS NULL GROUP BY w.job ORDER BY w.job""").fetchall()
        print(f"  {len(held):,} of them waited on a part no center could ship on the order day, and each came back after it shipped")
        require(held, "no repair waited on a back-ordered part, so that rule was not exercised")

        # Where one part was missing and no back-order held it, the wait is one draw from the made-up table.
        single = conn.execute("""SELECT julianday(w.repeat_visit_day) - julianday(w.order_day), COUNT(*)
                                 FROM waits w WHERE w.job IN (
                                   SELECT j.job FROM jobs j JOIN job_parts jp USING (job) JOIN trucks t ON t.tech = j.tech
                                   LEFT JOIN truck_stock s ON s.truck = t.truck AND s.part = jp.part
                                   LEFT JOIN waits x ON x.job = j.job
                                   LEFT JOIN backorders b ON b.part = jp.part AND b.starts <= x.order_day AND x.order_day < b.ships
                                   WHERE s.part IS NULL GROUP BY j.job HAVING COUNT(*) = 1 AND COUNT(b.part) = 0)
                                 GROUP BY 1""").fetchall()
        total = sum(n for _, n in single)
        off = {int(d): abs(n / total - world.LEAD_TIMES.get(int(d), 0)) for d, n in single}
        late = one(conn, "SELECT AVG(w.order_day > j.day) FROM waits w JOIN jobs j USING (job)")
        print(f"  {total:,} of them lacked one part that no back-order held: the share at each lead time is within "
              f"{max(off.values()):.1%} of the made-up table; ordered the next morning {late:.1%} against {world.ORDER_NEXT_DAY:.0%}")
        require(abs(sum(world.LEAD_TIMES.values()) - 1) < 1e-9, "the made-up lead-time shares do not add to one")
        require(dict(conn.execute("SELECT days, share FROM lead_times")) == world.LEAD_TIMES, "the lead_times table is not the code's")
        require(total > 10_000 and sorted(off) == sorted(world.LEAD_TIMES) and max(off.values()) < 0.01,
                f"the recorded lead times do not follow the made-up table: {off}")
        require(abs(late - world.ORDER_NEXT_DAY) < 0.01, "next-morning orders are off their made-up share")

        job = one(conn, f"SELECT job FROM waits WHERE job NOT IN ({', '.join(str(j) for j, _ in held)}) ORDER BY job LIMIT 1")
        aboard = one(conn, "SELECT job FROM jobs WHERE parts_on_truck = 1 ORDER BY job LIMIT 1")
        # A wait of no days breaks two rules at once, so its second rule is named beside it.
        for what, sql, rule, *also in (
                ("a repeat visit moved onto its order day", f"UPDATE waits SET repeat_visit_day = order_day WHERE job = {job}",
                 "came back on or before the order day", "arrived after no made-up lead time"),
                ("a repair with its parts given a wait", f"INSERT INTO waits SELECT job, day, date(day, '+2 days') FROM jobs WHERE job = {aboard}",
                 "had its parts and has dates"),
                ("a wait taken away", f"DELETE FROM waits WHERE job = {job}", "lacked a part and has no dates"),
                ("an order placed two days late", f"UPDATE waits SET order_day = date(order_day, '+2 days'), "
                 f"repeat_visit_day = date(repeat_visit_day, '+2 days') WHERE job = {job}",
                 "ordered before the visit or more than a day after"),
                ("a wait of a length the made-up table does not hold",
                 f"UPDATE waits SET repeat_visit_day = date(order_day, '+4 days') WHERE job = {job}", "arrived after no made-up lead time"),
                ("a part that arrives the day its back-order ends",
                 f"UPDATE waits SET repeat_visit_day = '{held[0][1]}' WHERE job = {held[0][0]}", "arrived before a center could ship it")):
            conn.execute(sql)
            caught = wait_faults(conn)
            conn.rollback()
            require(caught == {r: 1 for r in (rule, *also)}, f"control: {what} gave {caught}")
        require(not wait_faults(conn), "control: the planted faults were not undone")
        conn.close()
        print("  control: a repeat visit moved onto its order day, a repair with its parts given a wait, a wait taken away, "
              "an order placed two days late, a wait of four days and a part that arrives the day its back-order ends are "
              "each refused, on the rules it breaks and no other")


SPREADS = 4    # standard errors the recorded waits may sit from the engine's mean wait


def wait_by_hand(visit, missing, stuck, lead_times, next_day):
    """The mean wait of a repair that lacked these parts, worked a second way, apart from the engine. For each day
    after the visit it takes the chance every part has arrived, as the product of each part's own chance, and adds
    the chance that one has not."""
    from datetime import date, timedelta
    visit, mean = date.fromisoformat(visit), 0.0
    for lag, chance in ((0, 1 - next_day), (1, next_day)):
        ordered = str(visit + timedelta(days=lag))
        ready = [(date.fromisoformat(next((b for a, b in stuck.get(part, ()) if a <= ordered < b), ordered)) - visit).days
                 for part in missing]
        for day in range(max(ready, default=0) + max(lead_times)):
            arrived = 1.0
            for r in ready:
                arrived *= sum(share for days, share in lead_times.items() if r + days <= day)
            mean += chance * (1 - arrived)
    return mean


def waits_recount(visits, loads, needed, stuck, lead_times, next_day):
    """A replay's waiting figures counted again from its recorded visits and loads: the share of visits that lacked
    a part, the share that lacked one no center could ship that day, and the mean days a visit waits."""
    waiting = held = 0
    days = 0.0
    for day, tech, job, *_ in visits:
        missing = needed[job] - loads[day][tech]
        waiting += bool(missing)
        held += any(a <= day < b for part in missing for a, b in stuck.get(part, ()))
        days += wait_by_hand(day, missing, stuck, lead_times, next_day)
    return waiting / len(visits), held / len(visits), days / len(visits)


@check("cycle-time")
def cycle_time():
    """Parts cycle time is in the history and in every replay, agrees with a second way of working it, moves under
    planted changes, and reaches the report with the words its figures support."""
    import sqlite3
    from statistics import mean, stdev
    from staple import dispatch, examples, metrics, report, world
    conn = shared_world()
    results_written(conn)
    stuck, needed, aboard = defaultdict(list), defaultdict(set), defaultdict(set)
    for part, starts, ships in conn.execute("SELECT part, starts, ships FROM backorders"):
        stuck[part].append((starts, ships))
    for job, part in conn.execute("SELECT job, part FROM job_parts"):
        needed[job].add(part)
    for tech, part in conn.execute("SELECT t.tech, s.part FROM truck_stock s JOIN trucks t USING (truck)"):
        aboard[tech].add(part)
    leads, next_day = world.LEAD_TIMES, world.ORDER_NEXT_DAY

    # The mean wait the replay gives a repair, against what the history recorded for every repair that waited.
    history = [(day, needed[job] - aboard[tech], recorded) for job, day, tech, recorded in conn.execute(
        "SELECT j.job, j.day, j.tech, julianday(w.repeat_visit_day) - julianday(j.day) FROM waits w JOIN jobs j USING (job)")]
    engine = [world.expected_wait(day, missing, stuck) for day, missing, _ in history]
    gaps = [recorded - e for (_, _, recorded), e in zip(history, engine)]
    spread = stdev(gaps) / len(gaps) ** 0.5
    print(f"  {len(history):,} recorded waits average {mean(r for _, _, r in history):.3f} days and the engine's mean wait for "
          f"the same repairs {mean(engine):.3f}: {mean(gaps):+.4f} apart, where one standard error is {spread:.4f}")
    require(len(history) > 10_000 and abs(mean(gaps)) <= SPREADS * spread,
            f"the engine's mean wait sits more than {SPREADS} standard errors from the recorded waits")
    same_day = mean(recorded - world.expected_wait(day, missing, stuck, next_day=0) for day, missing, recorded in history)
    print(f"  control: an engine that orders every part on the day of the visit sits {same_day:+.3f} days from the record, "
          f"{abs(same_day) / spread:.0f} standard errors, and is refused")
    require(abs(same_day) > SPREADS * spread, "control: an engine with no next-morning orders passed against the record")

    # The same mean worked a second way, and the rule for a repair that lacks two parts.
    second = max(abs(e - wait_by_hand(day, missing, stuck, leads, next_day)) for (day, missing, _), e in zip(history, engine))
    pairs = [(day, missing) for day, missing, _ in history if len(missing) > 1]
    odd = [(day, missing) for day, missing in pairs
           if not max(world.expected_wait(day, {p}, stuck) for p in missing) - 1e-9
           <= world.expected_wait(day, missing, stuck) < sum(world.expected_wait(day, {p}, stuck) for p in missing)]
    print(f"  worked a second way, day by day, the mean wait differs by at most {second:.0e} days on those repairs; "
          f"{len(pairs):,} lacked two parts, and {len(pairs) - len(odd):,} of them wait at least as long as for either "
          "part and less than for both in turn")
    require(second < 1e-9, "the engine's mean wait and the second way of working it disagree")
    require(pairs and not odd, f"a repair that lacked two parts does not wait for the last of them: {odd[:2]}")
    longer = {days + 1: share for days, share in leads.items()}
    off = min(abs(e - wait_by_hand(day, missing, stuck, longer, next_day)) for (day, missing, _), e in zip(history, engine))
    print(f"  control: given lead times a day longer, the second way sits at least {off:.2f} days off on every repair")
    require(off > 1 - 1e-9, "control: lead times a day longer were not seen by the comparison")

    # Each replay's waiting figures, counted again from its recorded visits and loads.
    table = {policy: rest for policy, *rest in
             conn.execute("SELECT policy, parts_missing, backorder_missing, wait_per_call FROM dispatch")}
    when = {}
    for name in (dispatch.POLICIES[0][0], dispatch.POLICIES[-1][0]):
        jobs, run = recorded_replay(name)
        wanted = {j["job"]: j["needed"] for j in jobs}
        trucks = {day: rec["load"] for day, rec in run["days"].items()}
        counted = waits_recount(run["visits"], trucks, wanted, stuck, leads, next_day)
        bare = waits_recount(run["visits"], trucks, wanted, {}, leads, next_day)
        when[name] = (counted[2] / counted[0], bare[2] / bare[0])
        print(f"  {name}: counted again from {len(run['visits']):,} recorded visits, {counted[0]:.1%} wait, "
              f"{counted[1] / counted[0]:.1%} of those on a back-ordered part, {counted[2]:.4f} days a repair; the replay's "
              f"own figures differ by at most {max(abs(a - b) for a, b in zip(counted, table[name])):.0e}")
        require(all(abs(a - b) < 1e-9 for a, b in zip(counted, table[name])),
                f"the replay's waiting figures for {name} are not what its visits and loads give")
        require(bare[0] == counted[0] and bare[1] == 0 and bare[2] < counted[2],
                "control: emptying the back-order calendar did not shorten the recounted wait")
    (now_days, now_bare), (our_days, our_bare) = when.values()
    print(f"  a repair that waits, waits {our_days:.2f} days under Staple against {now_days:.2f} under the current rules; "
          f"with the back-order calendar emptied, {our_bare:.2f} against {now_bare:.2f}, so the gap goes from "
          f"{our_days - now_days:+.2f} days to {our_bare - now_bare:+.2f}")
    require(our_days > now_days and abs(our_bare - now_bare) < (our_days - now_days) / 2,
            "the report says a waiting repair waits longer under Staple because of back-orders, and the replays no "
            "longer show back-orders making up most of that gap: rewrite the card")

    # The query: planted changes must move its rows.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
    text = (metrics.SQL / "parts_cycle_time.sql").read_text(encoding="utf-8")

    def asked(db):
        cur = db.execute(text)
        return rows_by_key("parts_cycle_time", ([d[0] for d in cur.description], cur.fetchall()))

    rows = asked(conn)
    first, now_name, our_name = next(iter(rows)), dispatch.POLICIES[0][0], dispatch.POLICIES[-1][0]
    past, now, ours = rows[first], rows[now_name], rows[our_name]
    print(f"  the query: {first} {past['repairs_waiting_pct']}% wait, {past['days_when_waiting']:.2f} days each; the current "
          f"rules {now['repairs_waiting_pct']}% and {now['days_when_waiting']:.2f}; Staple {ours['repairs_waiting_pct']}% and "
          f"{ours['days_when_waiting']:.2f}")
    require(past["repairs"] == len(jobs), "the query's first row does not count every held-back repair")
    require(ours["repairs_waiting_pct"] < now["repairs_waiting_pct"], "Staple leaves no fewer repairs waiting than the current rules")
    copy = sqlite3.connect(":memory:")
    conn.backup(copy)
    copy.execute("UPDATE waits SET repeat_visit_day = date(repeat_visit_day, '+1 day')")
    copy.execute("UPDATE dispatch SET wait_per_call = 2 * wait_per_call WHERE policy = ?", (our_name,))
    later = asked(copy)
    copy.execute("DELETE FROM backorders")
    shipped = asked(copy)
    copy.close()
    moved = {"every repeat visit a day later": abs(later[first]["days_when_waiting"] - past["days_when_waiting"] - 1) < 0.011
                                               and later[first]["repairs_waiting_pct"] == past["repairs_waiting_pct"],
             "Staple's wait doubled in the replay's table": abs(later[our_name]["days_a_repair"] - 2 * ours["days_a_repair"]) < 0.011
                                                            and later[now_name] == now,
             "every back-order taken away": shipped[first]["waits_on_backorder_pct"] == 0 < past["waits_on_backorder_pct"]}
    print(f"  control: planted changes moved the query's rows as they should in {sum(moved.values())} of {len(moved)} cases")
    require(all(moved.values()), f"the cycle-time query did not move under a planted change: {moved}")

    # The page: the card reads the query, and stops where its words would no longer be true.
    week, _ = shared_week()
    stamp_text, day, _, _ = report.stamp()
    card = card_text(report.build_report(results, week, stamp_text, day), "parts_cycle_time")
    quoted = ([report.pct(r["repairs_waiting_pct"]) for r in (past, now, ours)]
              + [f"{r['days_when_waiting']:.2f}" for r in (past, now, ours)]
              + [report.pct(r["waits_on_backorder_pct"]) for r in (now, ours)]
              + [f"{r['days_a_repair']:.2f}" for r in (now, ours)])
    absent = [figure for figure in quoted if figure not in card]
    print(f"  the report's card carries {len(quoted) - len(absent)} of the {len(quoted)} figures its words rest on")
    require(not absent, f"the cycle-time card does not carry: {absent}")
    marked = changed(results, "parts_cycle_time", our_name, "repairs_waiting_pct", lambda v: 0.7)
    planted = lambda text: re.search(r"(?<![\d.])0\.7%", text)    # the planted share, and no longer figure that ends in it
    require(not planted(card) and planted(card_text(report.build_report(marked, week, stamp_text, day), "parts_cycle_time")),
            "control: a changed waiting share did not reach the card, so the card does not read the query")
    turned = changed(results, "parts_cycle_time", our_name, "waits_on_backorder_pct", lambda v: 0.0)
    try:
        report.build_report(turned, week, stamp_text, day)
        stopped = False
    except ValueError:
        stopped = True
    print(f"  control: a changed waiting share reaches the card, and a build whose query shows none of Staple's waits on "
          f"back-order {'stops' if stopped else 'goes on'}")
    require(stopped, "control: the report was built though its words contradict the cycle-time query")
    unexplained = changed(results, "parts_cycle_time", dispatch.POLICIES[2][0], "days_a_repair", lambda v: 0.0)
    try:
        report.build_report(unexplained, week, stamp_text, day)
        stopped = False
    except ValueError:
        stopped = True
    less = [name for name, r in rows.items() if name != first and r["days_a_repair"] < ours["days_a_repair"]]
    print(f"  {len(less)} rows wait less than Staple, and the card names each beside the calls it fixes: {less}; a build where "
          f"the skills-only row waits less than Staple {'stops' if stopped else 'goes on'}")
    require(all(name in card for name in less), f"the card does not name a row that waits less than Staple: {less}")
    require(stopped, "control: the report was built with a row waiting less than Staple that its words do not explain")

    example = next(x for x in examples.EXAMPLES if x.section == "cycle")
    print(f"  the worked example: {example.show(example.compute())}, as worked by hand")
    require(not example_faults([example]), "the cycle-time example's hand answer is not what the engine gives")


SCORED = ("fixes_per_call", "parts_missing", "line_fill", "wait_per_call", "stock_value", "part_jobs")    # the replay's figures behind the scorecard


def plan_recount(conn, jobs, run, stuck):
    """The replay's figures behind the scorecard, counted again from one plan's recorded visits and loads, the
    technicians' true rates and the parts' costs."""
    from staple import world
    needed, kind = {j["job"]: j["needed"] for j in jobs}, {j["job"]: j["type"] for j in jobs}
    rate = {(tech, appliance): r for tech, appliance, r in conn.execute("SELECT tech, type, success_rate FROM tech_skills")}
    cost = dict(conn.execute("SELECT part, cost FROM parts"))
    loads = {day: rec["load"] for day, rec in run["days"].items()}
    fixes = days = 0.0
    lacking = lines = aboard = whole_jobs = 0
    for day, tech, job, *_ in run["visits"]:
        have = needed[job] & loads[day][tech]
        whole = have == needed[job]
        fixes += whole * rate[(tech, kind[job])]
        lacking += not whole
        lines += len(needed[job])
        aboard += len(have)
        whole_jobs += bool(needed[job]) and whole
        days += wait_by_hand(day, needed[job] - have, stuck, world.LEAD_TIMES, world.ORDER_NEXT_DAY)
    truck_days = sum(len(trucks) for trucks in loads.values())
    stock = sum(cost[part] for trucks in loads.values() for parts in trucks.values() for part in parts)
    served = len(run["visits"])
    return {"fixes_per_call": fixes / len(jobs), "parts_missing": lacking / served, "line_fill": aboard / lines,
            "wait_per_call": days / served, "stock_value": stock / truck_days, "part_jobs": whole_jobs / truck_days}


def score_cells(page):
    """The scorecard as the report draws it: each row's cells as a reader sees them, with no markup."""
    import html
    table = re.search(r'<table class="score">.*?</table>', page, flags=re.S)
    if not table:
        return []
    return [[html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
            for row in re.findall(r"<tr>(.*?)</tr>", table.group(0).split("<tbody>")[1], flags=re.S)]


def score_faults(page, rows):
    """Where the report's scorecard parts ways with the query's rows: a row missing or out of place, a figure that is
    not the query's, a difference that is not Staple's figure less the current rules', or a row at mixed precision."""
    cells = score_cells(page)
    if page.count('id="scorecard"') != 1 or len(cells) != len(rows):
        return [f"the report draws {page.count('id=' + chr(34) + 'scorecard' + chr(34))} scorecards with {len(cells)} rows, for {len(rows)} measures"]
    faults = []
    for (name, r), (measure, now, ours, change, control) in zip(rows.items(), cells):
        if not measure.lower().startswith(name) or not measure.endswith(r["unit"]):
            faults.append(f"{name}: the row is headed '{measure}'")
            continue
        (x, px), (y, py), (d, pd), (c, pc) = (figure_of(cell) for cell in (now, ours, change, control))
        signed = -d if change.startswith("−") else d
        kept = float(re.search(r"keeps (-?\d+)%", control).group(1))
        if (x, y, signed, c, kept) != (r["current_rules"], r["staple"], r["difference"], r["control"], r["control_keeps_pct"]):
            faults.append(f"{name}: the page shows {now}, {ours}, {change} and {control}, which are not the query's figures")
        elif not px == py == pd == pc:
            faults.append(f"{name}: {now}, {ours}, {change} and {control} are not at one precision")
        elif abs(round(y - x, px) - signed) > 1e-9:
            faults.append(f"{name}: {ours} less {now} is not {change}")
    return faults


@check("scorecard")
def scorecard_check():
    """The five-measure scorecard is what the replay gives, and the report carries it with words its figures support."""
    import sqlite3
    from staple import dispatch, metrics, report
    conn = shared_world()
    results_written(conn)
    stuck = defaultdict(list)
    for part, starts, ships in conn.execute("SELECT part, starts, ships FROM backorders"):
        stuck[part].append((starts, ships))
    now_name, our_name, control_name = dispatch.POLICIES[0][0], dispatch.POLICIES[-1][0], dispatch.SHUFFLED_HISTORY

    # The replay's figures behind the five measures, counted again from each plan's recorded visits and loads.
    table = {policy: dict(zip(SCORED, figures)) for policy, *figures in
             conn.execute(f"SELECT policy, {', '.join(SCORED)} FROM dispatch")}
    counted = {}
    for name in (now_name, our_name, control_name):
        jobs, run = recorded_replay(name)
        counted[name] = plan_recount(conn, jobs, run, stuck)
        apart = max(abs(counted[name][k] - table[name][k]) for k in SCORED)
        print(f"  {name}: {len(SCORED)} figures counted again from {len(run['visits']):,} recorded visits and "
              f"{sum(len(rec['load']) for rec in run['days'].values()):,} truck loads; the replay's own differ by at most {apart:.0e}")
        require(apart < 1e-9, f"the replay's scorecard figures for {name} are not what its visits and loads give")
    jobs, run = recorded_replay(our_name)
    lighter = dict(run, days={day: dict(rec, load={tech: set(sorted(parts)[1:]) for tech, parts in rec["load"].items()})
                              for day, rec in run["days"].items()})
    less = plan_recount(conn, jobs, lighter, stuck)
    moved = [k for k in SCORED if less[k] != counted[our_name][k]]
    print(f"  control: with one part taken off every truck, the recount moves {len(moved)} of {len(SCORED)} figures; the "
          f"fill rate falls from {counted[our_name]['line_fill']:.1%} to {less['line_fill']:.1%}")
    require(less["line_fill"] < counted[our_name]["line_fill"] and less["stock_value"] < counted[our_name]["stock_value"]
            and less["parts_missing"] > counted[our_name]["parts_missing"],
            "control: taking a part off every truck did not move the recount")

    # The query's rows: Staple the better on every measure, the difference from the figures shown, the control's share.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = metrics.run(conn, Path(tmp))
    text = (metrics.SQL / "scorecard.sql").read_text(encoding="utf-8")

    def asked(db):
        cur = db.execute(text)
        return rows_by_key("scorecard", ([d[0] for d in cur.description], cur.fetchall()))

    rows = asked(conn)
    for name, r in rows.items():
        print(f"  {name}, {r['unit']}: {r['current_rules']} under the current rules, {r['staple']} under Staple, "
              f"{r['difference']:+} apart; the control {r['control']}, keeping {r['control_keeps_pct']:.0f}%")
    worse = [name for name, r in rows.items() if not (r["difference"] > 0 if r["better_when"] == "higher" else r["difference"] < 0)]
    require(len(rows) == 5 and not worse, f"the scorecard does not show five measures, each the better under Staple: {worse}")
    keeps = [r["control_keeps_pct"] for r in rows.values()]
    require(50 < min(keeps) and max(keeps) < 100,
            f"the scorecard's words say the control keeps most of each gain and less than all of it, and it keeps {keeps}")
    copy = sqlite3.connect(":memory:")
    conn.backup(copy)
    copy.execute("UPDATE dispatch SET line_fill = line_fill / 2 WHERE policy = ?", (our_name,))
    copy.execute("UPDATE assumptions SET value = 2 * value WHERE name = 'repeat_visit'")
    copy.execute("UPDATE dispatch SET fixes_per_call = (SELECT fixes_per_call FROM dispatch WHERE policy = ?) WHERE policy = ?",
                 (now_name, control_name))
    later = asked(copy)
    copy.close()
    fill, back, fix, wait = (later[m] for m in ("fill rate", "inventory return", "first-time fix", "parts cycle time"))
    planted = {"Staple's fill rate halved": abs(fill["staple"] - rows["fill rate"]["staple"] / 2) < 0.051
                                            and fill["current_rules"] == rows["fill rate"]["current_rules"],
               "a repeat visit priced at double": all(abs(back[c] - 2 * rows["inventory return"][c]) < 0.011
                                                      for c in ("current_rules", "staple", "control"))
                                                  and abs(back["control_keeps_pct"] - rows["inventory return"]["control_keeps_pct"]) <= 1,
               "the control given the current rules' fixes": fix["control_keeps_pct"] == 0 and fix["staple"] == rows["first-time fix"]["staple"]}
    print(f"  control: planted changes moved the query's rows as they should in {sum(planted.values())} of {len(planted)} cases, "
          f"and left the cycle-time row {'alone' if wait == rows['parts cycle time'] else 'changed'}")
    require(all(planted.values()) and wait == rows["parts cycle time"], f"the scorecard query did not move as it should: {planted}")

    # The page: the scorecard once, every figure the query's, and a build that stops where its words would be untrue.
    week, _ = shared_week()
    stamp_text, day, _, _ = report.stamp()
    page = report.build_report(results, week, stamp_text, day)
    faults = score_faults(page, rows)
    print(f"  the report draws the scorecard once, with {len(score_cells(page))} rows; faults against the query: {faults or 'none'}")
    require(not faults, f"the report's scorecard is not the query's: {faults}")
    off = report.build_report(changed(results, "scorecard", "fill rate", "staple", lambda v: v + 1), week, stamp_text, day)
    caught = score_faults(off, rows)
    stopped = []
    for what, column, value in (("first-time fix", "difference", lambda v: -v), ("inventory return", "control_keeps_pct", lambda v: 104.0)):
        try:
            report.build_report(changed(results, "scorecard", what, column, value), week, stamp_text, day)
        except ValueError:
            stopped.append(what)
    print(f"  control: a page whose fill rate under Staple is a point off is refused ({len(caught)} fault), and a build "
          f"stops where a measure is worse under Staple or the control passes Staple: {len(stopped)} of 2")
    require(len(caught) == 1 and caught[0].startswith("fill rate"), f"control: a changed figure on the scorecard gave {caught}")
    require(len(stopped) == 2, "control: the report was built though its scorecard's words contradict the query")


def buyer_by_hand(case, repeat, move, fee, fast, wait):
    """One case's four prices from the sentences of the methods page, worked apart from the engine."""
    risk, days = case["stuck"] * repeat, case["days_left"]
    quick = days if days < fast else fast
    left = case["stuck"] - sum(odds for _, odds in case["waiting"])
    return {"send anyway": risk, "expedite": fee + risk * quick / days,
            "move one": None if case["move"] is None else move + move + case["move"]["stuck"] * repeat,
            "rebook": (None if not case["waiting"] else
                       wait * days * len(case["waiting"]) + (left if left > 0 else 0.0) * repeat)}


def buyer_recount(conn, held, nearby=None):
    """What the buyer's cases rest on, counted again from the tables: for each back-ordered part, the smallest share
    of its appliance's calls any single move leaves stuck, and for each day and part, the booked calls waiting."""
    import math
    from staple import load, world
    from staple.predict import Predictor
    nearby = load.NEARBY_MINUTES if nearby is None else nearby
    home = dict(conn.execute("SELECT tech, home_zone FROM techs"))
    certified = {t for (t,) in conn.execute("SELECT tech FROM techs WHERE certified = 1")}
    locked = {p for (p,) in conn.execute("SELECT part FROM parts WHERE locked = 1")}
    kind = dict(conn.execute("SELECT part, type FROM parts"))
    stock = defaultdict(set)
    for tech, part in conn.execute("SELECT t.tech, s.part FROM truck_stock s JOIN trucks t USING (truck)"):
        stock[tech].add(part)
    spans = conn.execute("SELECT part, starts, ships FROM backorders").fetchall()
    calls = defaultdict(Counter)      # appliance, then (zone, technician): calls in the training history
    for appliance, zone, tech, n in conn.execute(
            "SELECT type, zone, tech, COUNT(*) FROM jobs WHERE split = 'train' GROUP BY type, zone, tech"):
        calls[appliance][(zone, tech)] = n

    def near(a, b):
        (xa, ya), (xb, yb) = world.ZONES[a], world.ZONES[b]
        return world.BETWEEN_ZONES + world.MINUTES_A_MILE * world.ZONE_MILES * math.hypot(xa - xb, ya - yb) <= nearby

    def share(appliance, holders):
        """The share of an appliance's calls no holder covers: not a holder's own, and in a zone no holder is near."""
        stuck = sum(n for (zone, tech), n in calls[appliance].items()
                    if tech not in holders and not any(near(home[h], zone) for h in holders))
        return stuck / sum(calls[appliance].values())

    moves = {}
    for part in {p for p, _, _ in spans}:
        holders = {t for t in home if part in stock[t]}
        takers = [t for t in home if t not in holders and (part not in locked or t in certified)]
        tried = [share(kind[part], holders - {giver} | {taker}) for giver in holders for taker in takers]
        moves[part] = min(tried) if tried else None
    predictor, waiting = Predictor(conn), Counter()
    for j in held:
        stuck = {p for p, starts, ships in spans if starts <= j["day"] < ships}
        if j["unplanned"] or not stuck:
            continue
        odds = predictor.odds(j["type"], j["family"], j["problem"])
        for part in stuck:
            if (odds.get(part, 0.0) >= load.BACKORDER_FLAG and part not in stock[j["tech"]]
                    and not (part in locked and j["tech"] not in certified)
                    and not any(part in stock[o] and near(home[o], j["zone"]) for o in home if o != j["tech"])):
                waiting[(j["day"], part)] += 1
    return moves, waiting


@check("buyer-scorer")
def buyer_scorer():
    from staple import buyer, examples, load
    from staple.backtest import test_jobs
    conn = shared_world()
    found = buyer.run(conn)
    cases, scores = found["cases"], found["scores"]
    fleet = load.Fleet(conn)
    days = sorted({c["day"] for c in cases})
    require(sorted((c["day"], c["part"]) for c in cases) == sorted((d, p) for d in days for p in fleet.stuck(d)),
            "the buyer's cases are not every back-ordered part of every held-back day, once each")
    priced = [buyer.prices(c) for c in cases]
    best = Counter(buyer.cheapest(p) for p in priced)
    print(f"  {len(cases):,} back-ordered part-days priced four ways; the cheapest is "
          + ", ".join(f"{a} on {best[a]}" for a in buyer.ACTIONS))
    require(all(best[a] for a in buyer.ACTIONS), "one of the four actions is never the cheapest, so it is no real choice")

    dollars = (load.REPEAT_VISIT, load.MOVE, buyer.EXPEDITE, buyer.EXPEDITE_DAYS, buyer.WAIT_DAY)

    def differ(mine, theirs):
        return any((mine[a] is None) != (theirs[a] is None) or (mine[a] is not None and abs(mine[a] - theirs[a]) > 1e-9)
                   for a in buyer.ACTIONS)
    off = sum(differ(p, buyer_by_hand(c, *dollars)) for c, p in zip(cases, priced))
    moved = sum(differ(p, buyer_by_hand(c, dollars[0], dollars[1], 2 * dollars[2], *dollars[3:])) for c, p in zip(cases, priced))
    print(f"  the four prices worked again apart from the engine: {len(cases) - off} of {len(cases)} agree; "
          f"control: with the fee doubled, {moved} of {len(cases)} disagree")
    require(not off, "a price does not match the arithmetic the methods page states")
    require(moved == len(cases), "the price recount cannot tell a fee twice as high")
    table = conn.execute("SELECT day, part, waiting_calls, send_anyway, expedite, move_one, rebook, cheapest FROM buyer_prices "
                         "ORDER BY day, part").fetchall()
    require(table == sorted((c["day"], c["part"], len(c["waiting"]), *(p[a] for a in buyer.ACTIONS), buyer.cheapest(p))
                            for c, p in zip(cases, priced)), "buyer_prices does not hold the prices the engine computed")

    held = test_jobs(conn)
    load.mark_unplanned(held)
    moves, waiting = buyer_recount(conn, held)
    move_off = [c for c in cases if (c["move"] is None) != (moves[c["part"]] is None)
                or (c["move"] and abs(c["move"]["stuck"] - c["expected"] * moves[c["part"]]) > 1e-9)]
    wait_off = [c for c in cases if len(c["waiting"]) != waiting[(c["day"], c["part"])]]
    movable, booked = sum(c["move"] is not None for c in cases), sum(len(c["waiting"]) for c in cases)
    print(f"  counted again from the tables: the best single move on {movable} cases, {movable - len(move_off)} agree; "
          f"{booked} waiting calls on {sum(bool(c['waiting']) for c in cases)} cases, {len(cases) - len(wait_off)} of {len(cases)} cases agree")
    require(not move_off and not wait_off, "a case's best move or waiting calls do not match the recount from the tables")
    tighter, _ = buyer_recount(conn, held, nearby=0)
    _, none = buyer_recount(conn, [dict(j, unplanned=True) for j in held])
    blind = [c for c in cases if c["move"] and abs(c["move"]["stuck"] - c["expected"] * tighter[c["part"]]) > 1e-9]
    print(f"  control: with no truck counted as nearby the recount disagrees on {len(blind)} moves, and with every call "
          f"marked unplanned it finds {sum(none.values())} waiting calls against {booked}")
    require(blind and booked and not sum(none.values()), "the recount cannot see a change in what counts as nearby or booked")

    widest = max(range(len(cases)), key=lambda i: priced[i][buyer.SEND] - priced[i][buyer.cheapest(priced[i])])
    gap = priced[widest][buyer.SEND] - priced[widest][buyer.cheapest(priced[widest])]
    slip = buyer.score(cases, lambda c: buyer.SEND if c is cases[widest] else buyer.cheapest(buyer.prices(c)))
    no_holder = next(c for c in cases if c["move"] is None)
    barred = [buyer.score([no_holder], lambda c: said) for said in (buyer.SHIFT, "wait and see")]
    print(f"  the scorer: the cheapest action every time loses ${scores[buyer.ARITHMETIC]['lost']:.2f}; one wrong choice, on "
          f"{cases[widest]['part']} on {cases[widest]['day']}, loses ${slip['lost']:,.2f}, its own gap; a move where no truck holds "
          f"the part and an action that is not one of the four are each counted and charged as sending anyway")
    require(scores[buyer.ARITHMETIC]["lost"] == 0 and scores[buyer.ARITHMETIC]["invalid"] == 0,
            "taking the cheapest action every time does not score zero")
    require(gap > 0 and abs(slip["lost"] - gap) < 1e-9, "one wrong choice does not lose exactly its own gap")
    require(all(b["invalid"] == 1 and b["cost"] == buyer.prices(no_holder)[buyer.SEND] for b in barred),
            "a choice that cannot be carried out is not counted and charged as sending anyway")
    other = buyer.other_case(len(cases))
    require(sorted(other) == sorted(other.values()) == list(range(len(cases))) and all(a != b for a, b in other.items()),
            "the control lets a case read its own facts, or leaves one out")
    for name, s in scores.items():
        print(f"  {name}: ${s['cost']:,.0f} spent, ${s['lost']:,.0f} over the cheapest, ${s['lost'] / s['decisions']:.2f} a decision; "
              f"{s['invalid'] or 'none'} could not be carried out")
        require(name == buyer.ARITHMETIC or s["lost"] > 0, f"{name} loses nothing, so the scorer cannot tell it from the cheapest")
    require(conn.execute("SELECT decider, decisions, invalid, cost, lost FROM buyer_score").fetchall()
            == [(n, s["decisions"], s["invalid"], s["cost"], s["lost"]) for n, s in scores.items()],
            "buyer_score does not hold the scores the engine computed")
    example = next(x for x in examples.EXAMPLES if x.section == "buyer")
    require(not example_faults([example]), "the worked example of the buyer's prices no longer matches the engine")
    print(f"  the worked example, by hand: {example.show(example.by_hand)}; the engine agrees")


def scripted_buyer(script="reads", skip=()):
    """A scripted stand-in for Claude as the buyer: a function of one request that answers as the API would. It
    knows only what the request holds, the opening line and the answers its own tool calls got. As a reader it
    asks for the lookups, prices the four actions from what they returned and commits the cheapest; skip names
    lookups it never asks for. The other scripts each break the loop's agreed form in one way."""
    import itertools
    import json
    from staple import agent, buyer, load
    ids = itertools.count(1)
    dollars = (load.REPEAT_VISIT, load.MOVE, buyer.EXPEDITE, buyer.EXPEDITE_DAYS, buyer.WAIT_DAY)

    def call(name, **given):
        return {"type": "tool_use", "id": f"toolu_{next(ids):06d}", "name": name, "input": given}

    def reply(blocks, stop="tool_use"):
        thinking = {"type": "thinking", "thinking": "", "signature": f"signed-{next(ids)}"}
        return {"role": "assistant", "content": [thinking, *blocks], "stop_reason": stop, "usage": dict(STAND_IN_USAGE)}

    def respond(body):
        messages = body["messages"]
        part = messages[0]["content"].split("The part ", 1)[1].split(" is back-ordered", 1)[0]
        asked = {b["id"]: b["name"] for m in messages if m["role"] == "assistant" for b in m["content"]
                 if b["type"] == "tool_use"}
        got = {asked[r["tool_use_id"]]: json.loads(r["content"]) for m in messages
               if m["role"] == "user" and isinstance(m["content"], list) for r in m["content"] if not r.get("is_error")}
        turn = sum(m["role"] == "assistant" for m in messages)
        if script == "never commits":
            return reply([call(agent.FORECAST, part=part)])
        if script == "talks first" and turn == 0:
            return reply([{"type": "text", "text": "I would expedite it."}], "end_turn")
        if script == "bad calls first" and turn == 0:
            return reply([call(agent.FORECAST, part="no such part"),
                          call(agent.COMMIT, action="wait and see", reason="It may ship sooner.", note="Hold on.")])
        if script == "cut off":
            return reply([call(agent.FORECAST, part=part)], "max_tokens")
        if script == "declines":
            return reply([], "refusal")
        wanted = [n for n in (agent.FORECAST, agent.TRUCKS, agent.WAITING) if n not in skip]
        if any(n not in got for n in wanted):
            return reply([call(n, part=part) for n in wanted])
        forecast, trucks, waiting = got[agent.FORECAST], got.get(agent.TRUCKS), got.get(agent.WAITING)
        move = trucks and trucks["move_on_offer"]
        seen = {"stuck": forecast["calls_expected_to_be_stuck"], "days_left": forecast["days_until_it_ships"],
                "move": move and {"stuck": move["calls_expected_to_be_stuck_after"]},
                "waiting": [(c["call"], c["odds_it_needs_the_part"]) for c in (waiting or {"calls_waiting": []})["calls_waiting"]]}
        priced = buyer_by_hand(seen, *dollars)
        action = min((a for a in buyer.ACTIONS if priced[a] is not None), key=lambda a: priced[a])
        note = f"{part} ships on {forecast['ships_on']}."
        if action == buyer.SHIFT:
            note += f" Move one from {move['from_truck']} to {move['to_truck']}."
        return reply([call(agent.COMMIT, action=action, reason=f"It is the cheapest, at ${priced[action]:.2f}.", note=note)])
    return respond


STAND_IN_USAGE = {"input_tokens": 400, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 900, "output_tokens": 350}


@check("buyer-loop")
def buyer_loop():
    import json
    from staple import agent, buyer, triage
    conn = shared_world()
    cases = buyer.cases(conn)
    model, other_model = agent.MODELS
    best = [buyer.cheapest(buyer.prices(c)) for c in cases]

    def put(case, respond):
        """One case through the loop: the decision, and every request and reply as they crossed."""
        sent, came = [], []

        def ask(body):
            sent.append(json.loads(json.dumps(body)))
            came.append(respond(body))
            return came[-1]
        return agent.decide(case, ask, model), sent, came

    # Every case, read by a stand-in that sees only what the loop sends it.
    reader = scripted_buyer()
    runs = [put(c, reader) for c in cases]
    answers = {agent.name_of(c): d for c, (d, _, _) in zip(cases, runs)}
    agree = sum(d["action"] == b for (d, _, _), b in zip(runs, best))
    lookups = [agent.FORECAST, agent.TRUCKS, agent.WAITING]
    score = buyer.score(cases, agent.chooser(answers))
    short = scripted_buyer(skip=(agent.TRUCKS,))
    without = {agent.name_of(c): put(c, short)[0] for c in cases}
    short_agree = sum(without[agent.name_of(c)]["action"] == b for c, b in zip(cases, best))
    short_score = buyer.score(cases, agent.chooser(without))
    print(f"  a stand-in that reads only the lookups' answers and prices the four actions from them names the engine's "
          f"cheapest on {agree} of {len(cases)} cases, in {sum(d['rounds'] for d, _, _ in runs)} requests, and loses "
          f"${score['lost']:.2f}; control: never asking about the trucks, it agrees on {short_agree} and loses "
          f"${short_score['lost']:,.0f}")
    require(agree == len(cases) and score["lost"] == 0 and not score["invalid"],
            "the lookups do not carry enough to reach the engine's cheapest action")
    require(all(d["rounds"] == 2 and d["lookups"] == lookups and "stopped" not in d for d, _, _ in runs),
            "a case read by the stand-in did not take its three lookups and a commit, in two requests")
    require(short_agree < len(cases) and short_score["lost"] > 0, "control: a reader that never asks about the trucks passes")

    # What a lookup returns is the case's own facts and nothing else, and nothing else in a request differs by case.
    def by_hand(case):
        ships = datetime.date.fromisoformat(case["day"]) + datetime.timedelta(days=case["days_left"])
        move = case["move"] and {"from_truck": case["move"]["giver"], "to_truck": case["move"]["taker"],
                                 "calls_expected_to_be_stuck_after": round(case["move"]["stuck"], agent.PLACES)}
        return {agent.FORECAST: {"part": case["part"], "today": case["day"], "ships_on": str(ships),
                                 "days_until_it_ships": case["days_left"],
                                 "calls_expected_to_need_it": round(case["expected"], agent.PLACES),
                                 "calls_expected_to_be_stuck": round(case["stuck"], agent.PLACES)},
                agent.TRUCKS: {"trucks_holding_it": case["holders"], "zones_with_no_holder_nearby": case["open_zones"],
                               "move_on_offer": move},
                agent.WAITING: {"calls_waiting": [{"call": job, "odds_it_needs_the_part": round(odds, agent.PLACES)}
                                                  for job, odds in case["waiting"]]}}

    def beyond_facts(case, sent):
        """The lookups whose answer in the last request is not exactly the case's own facts."""
        asked = {b["id"]: b["name"] for m in sent[-1]["messages"] if m["role"] == "assistant" for b in m["content"]
                 if b["type"] == "tool_use"}
        told = {asked[r["tool_use_id"]]: json.loads(r["content"]) for m in sent[-1]["messages"]
                if m["role"] == "user" and isinstance(m["content"], list) for r in m["content"]}
        return [name for name, facts in by_hand(case).items() if told.get(name) != facts]

    leaks = [agent.name_of(c) for c, (_, sent, _) in zip(cases, runs) if beyond_facts(c, sent)]
    frame = [agent.name_of(c) for c, (_, sent, _) in zip(cases, runs) for body in sent
             if body["system"][0]["text"] != agent.SYSTEM or body["tools"] != agent.TOOLS or body["model"] != model
             or "tool_choice" in body or body["messages"][0] != {"role": "user", "content": agent.OPENING.format(
                 day=c["day"], part=c["part"])}]
    honest = agent.facts
    agent.facts = lambda case, lookup: {**honest(case, lookup), **({"dollars_at_risk": case["at_risk"]}
                                                                  if lookup == agent.FORECAST else {})}
    try:
        planted = sum(bool(beyond_facts(c, put(c, reader)[1])) for c in cases[:40])
    finally:
        agent.facts = honest
    print(f"  what the lookups returned on {len(cases)} cases: {len(leaks)} answers hold anything but the case's own counts, "
          f"days and odds; every request carries the same instructions and tools, and only its opening line and the "
          f"lookups' answers differ by case; control: a forecast that also gives the dollars at risk is refused on "
          f"{planted} of 40 cases")
    require(not leaks and not frame, f"a lookup returns more than the case's facts, or a request differs by case: {(leaks + frame)[:3]}")
    require(planted == 40, "control: a lookup that also gives a price passes")
    echoed = sum(sent[1]["messages"][1] == {"role": "assistant", "content": came[0]["content"]} for _, sent, came in runs)
    require(echoed == len(cases), "a reply does not go back whole, with its thinking, in the next request")

    # Each way a reply can break the agreed form.
    case = next(c for c in cases if c["move"] and c["waiting"])
    cheapest = buyer.cheapest(buyer.prices(case))
    bad, bad_sent, _ = put(case, scripted_buyer("bad calls first"))
    errors = [r for r in bad_sent[1]["messages"][-1]["content"] if r.get("is_error")]
    shapes = {"a tool that does not exist": {"name": "price_everything", "input": {"part": case["part"]}},
              "an input that is not a set of fields": {"name": agent.FORECAST, "input": [case["part"]]},
              "a missing field": {"name": agent.COMMIT, "input": {"action": buyer.SEND, "reason": "It is cheapest."}},
              "a field too many": {"name": agent.WAITING, "input": {"part": case["part"], "day": case["day"]}},
              "an empty reason": {"name": agent.COMMIT, "input": {"action": buyer.SEND, "reason": " ", "note": "Go."}},
              "a part that is not a text": {"name": agent.TRUCKS, "input": {"part": 7}}}
    refused = []
    for name, shape in shapes.items():
        try:
            agent.answer(case, shape)
        except agent.BadCall:
            refused.append(name)
    require(agent.answer(case, {"name": agent.FORECAST, "input": {"part": case["part"]}}) == by_hand(case)[agent.FORECAST],
            "control: a lookup in the agreed form is refused")
    print(f"  a reply that asks about another part and commits an action that is not one of the four: {len(errors)} errors go "
          f"back in plain words, and the case still ends on {bad['action']} in {bad['rounds']} requests; {len(refused)} of "
          f"{len(shapes)} other malformed calls are refused, and one in the agreed form is answered")
    require(len(errors) == 2 and "this case is about" in errors[0]["content"] and "must be one of" in errors[1]["content"]
            and bad["action"] == cheapest and bad["rounds"] == 3, "a bad tool call is not answered with an error the next reply can use")
    require(len(refused) == len(shapes), f"a malformed call is answered: {sorted(set(shapes) - set(refused))}")

    endless, endless_sent, _ = put(case, scripted_buyer("never commits"))
    charged = buyer.score([case], agent.chooser({agent.name_of(case): endless}))
    talk, talk_sent, _ = put(case, scripted_buyer("talks first"))
    cut, _, _ = put(case, scripted_buyer("cut off"))
    declined, _, _ = put(case, scripted_buyer("declines"))
    print(f"  a reply that never commits is stopped after {endless['rounds']} requests and scored as sending anyway; one that "
          f"only talks is asked to commit and does, in {talk['rounds']}; one cut off at the token limit, and one that "
          f"declines, each end the case undecided with no call run")
    require(endless["stopped"] == agent.ROUND_LIMIT and endless["action"] is None and endless["rounds"] == agent.MAX_ROUNDS
            and len(endless_sent) == agent.MAX_ROUNDS, "a case that never commits is not stopped at the round limit")
    require(charged["invalid"] == 1 and charged["cost"] == buyer.prices(case)[buyer.SEND],
            "an undecided case is not counted and charged as sending anyway")
    require(talk["action"] == cheapest and talk["rounds"] == 3 and talk_sent[1]["messages"][-1]["content"] == agent.NUDGE,
            "a reply with no tool call is not asked to commit")
    require((cut["stopped"], cut["lookups"], cut["action"]) == (agent.CUT_OFF, [], None)
            and (declined["stopped"], declined["action"]) == (agent.DECLINED, None),
            "a reply cut off or declined does not end the case undecided, or its half-made call was run")

    # A run over the wire: saved by model and prompt, picked up where it stopped, and plain about each failure.
    few, usage = cases[:6], STAND_IN_USAGE
    into, out = agent.PRICES[model]
    a_request = (usage["input_tokens"] * into + usage["cache_read_input_tokens"] * into * agent.CACHE_READ
                 + usage["output_tokens"] * out) / 1e6
    home = agent.DATA
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        agent.DATA = Path(folder)
        try:
            with stand_in_api(scripted_buyer()) as (url, seen):
                first = agent.run(few, model, "test-key", 5.0, url)
                once, headers, body = seen["requests"], seen["headers"], seen["body"]
                again = agent.run(few, model, "test-key", 5.0, url)
                twice = seen["requests"]
                agent.run(few[:2], other_model, "test-key", 5.0, url)
                file = json.loads(agent.path(model).read_text(encoding="utf-8"))
                require(headers.get("x-api-key") == "test-key" and headers.get("anthropic-version") == triage.API_VERSION
                        and body["model"] == model and body["tools"] == agent.TOOLS and "tool_choice" not in body,
                        "a request over the wire lacks the key, the version, the model or the tools")
                require(once == 2 * len(few) and twice == once and again == first == agent.saved(model),
                        "a second run asks again for decisions that are saved")
                require(file["prompt_id"] == agent.PROMPT_ID and file["model"] == model
                        and sorted(file["answers"]) == sorted(agent.name_of(c) for c in few) and len(agent.saved(other_model)) == 2,
                        "the answers are not saved by model and prompt")
                require(all(a["action"] == buyer.cheapest(buyer.prices(c)) and a["reason"] and a["note"] and a["rounds"] == 2
                            for c, a in ((c, file["answers"][agent.name_of(c)]) for c in few)),
                        "a saved answer lacks its action, reason or note")
                require(a_request > 0 and abs(agent.cost(model, first) - 2 * len(few) * a_request) < 1e-12
                        and abs(file["spent"] - 2 * len(few) * a_request) < 1e-12,
                        "the dollars spent are not the saved token counts at the listed prices")
                agent.path(model).write_text(json.dumps({**file, "prompt_id": "another prompt"}), encoding="utf-8")
                before, stale = seen["requests"], agent.saved(model)
                try:
                    agent.run(few, model, "test-key", 5.0, url)
                    kept_safe = None
                except SystemExit as halt:
                    kept_safe = str(halt.code)
                require(stale is None and kept_safe and "different prompt" in kept_safe and seen["requests"] == before,
                        "answers made with another prompt are read as this prompt's, or are replaced without being asked")
                require(agent.run(few, model, "test-key", 5.0, url, fresh=True) == first == agent.saved(model),
                        "a fresh run does not replace answers made with another prompt")
            print(f"  over the wire, {len(few)} cases take {once} requests and are saved under the model and the prompt's "
                  f"name; a second run asks for {twice - once}; answers made with another prompt are not read, and are "
                  f"not replaced until asked; the saved token counts price the run at ${2 * len(few) * a_request:.6f}, as by hand")

            # A failure part way: one case's request fails, the rest stay saved, and the next run asks only for that one.
            sore, healthy = few[-1], scripted_buyer()

            def fails_on_one(body):
                if body["messages"][0]["content"] == agent.OPENING.format(day=sore["day"], part=sore["part"]):
                    return "<html>busy</html>"
                return healthy(body)
            agent.path(model).unlink()
            with stand_in_api(fails_on_one) as (url, _):
                try:
                    agent.run(few, model, "test-key", 5.0, url)
                    part_way = None
                except SystemExit as halt:
                    part_way = str(halt.code)
            left = agent.saved(model)
            with stand_in_api(healthy) as (url, seen):
                whole, resumed = agent.run(few, model, "test-key", 5.0, url), seen["requests"]
            require(part_way and "not JSON" in part_way and f"{len(few) - 1} of {len(few)} decisions are saved" in part_way
                    and sorted(left) == sorted(agent.name_of(c) for c in few[:-1]),
                    f"a run that fails part way does not keep the decisions made before it: {part_way}")
            require(whole == first and resumed == 2, "the next run does not ask only for the case that failed")

            def stop(reply, status=200, down=False):
                """The sentence a run stops on when the API answers with reply, or when nothing answers."""
                with stand_in_api(reply, status) as (url, _):
                    if not down:
                        try:
                            agent.run(cases[6:7], other_model, "test-key", 5.0, url)
                        except SystemExit as halt:
                            return str(halt.code)
                        return None
                try:    # the stand-in is gone by now, so nothing answers at its address
                    agent.run(cases[6:7], other_model, "test-key", 5.0, url)
                except SystemExit as halt:
                    return str(halt.code)
                return None
            stops = {"answered 500": stop({"error": {"message": "an internal error"}}, 500),
                     "ANTHROPIC_API_KEY": stop({"error": {"message": "invalid x-api-key"}}, 401),
                     "not JSON": stop("<html>busy</html>"), "could not be reached": stop({}, down=True)}
            require(all(said and said.startswith("the buyer run stopped: ") and words in said and "Traceback" not in said
                        for words, said in stops.items()), f"a failed run does not stop in one plain sentence: {stops}")
            require(stop(scripted_buyer()) is None, "control: a run against a healthy stand-in stops")

            waits = {}
            for label, status, good_after in (("busy twice", 529, 2), ("always at its limit", 429, None), ("a bad key", 401, None)):
                waited, tries = [], []

                def send(body, status=status, good_after=good_after, tries=tries):
                    tries.append(1)
                    if good_after is None or len(tries) <= good_after:
                        raise triage.LiveError("no", status)
                    return {"content": []}
                try:
                    agent.patient(send, sleep=waited.append)({})
                    waits[label] = (waited, True)
                except triage.LiveError:
                    waits[label] = (waited, False)
            require(waits == {"busy twice": (list(agent.WAITS[:2]), True), "always at its limit": (list(agent.WAITS), False),
                              "a bad key": ([], False)}, f"a busy API is not asked again after a wait, or another failure is: {waits}")
            print(f"  a failure part way keeps {len(left)} of {len(few)} decisions and the next run asks only for the one that "
                  f"failed; a 500, a bad key, a reply that is not JSON and no answer at all each stop the run in one plain "
                  f"sentence; a busy API is asked again after {' and '.join(str(w) for w in agent.WAITS[:2])} seconds, and a "
                  f"bad key is not")

            # The cap: a request goes out only while the most it could cost still fits under it.
            request = runs[0][1][-1]
            most = agent.ceiling(other_model, request)

            def silent(body):
                return {"content": []}       # a reply that does not say what it used
            budget, passed = agent.Budget(other_model, 3.5 * most), 0
            try:
                for _ in range(10):
                    budget.ask(request, silent)
                    passed += 1
            except agent.CapReached:
                pass

            def beside(cap):
                """Whether a second request is let out while a first is still out, under this cap."""
                held = agent.Budget(other_model, cap)

                def second(body):
                    try:
                        held.ask(request, silent)
                    except agent.CapReached:
                        return {"content": [], "second": False}
                    return {"content": [], "second": True}
                return held.ask(request, second)["second"]
            counted = agent.Budget(model, 1.0)
            counted.ask(request, lambda body: {"usage": dict(usage)})
            into_other, out_other = agent.PRICES[other_model]
            require(most >= (len(json.dumps(request)) * into_other + agent.MAX_TOKENS * out_other) / 1e6,
                    "the most a request can cost is under its characters as tokens and a full reply")
            require(passed == 3 and abs(budget.spent - 3 * most) < 1e-12 and budget.spent <= budget.cap,
                    "the cap is passed, or a reply that says nothing of its use is charged less than the most it could cost")
            require(not beside(1.5 * most) and beside(2.5 * most), "the cap does not hold room for a request still out")
            require(abs(counted.spent - a_request) < 1e-15, "a reply's own token counts are not what the cap charges")
            # A case cut short between its two requests: what its first request cost stays on the books.
            agent.path(model).unlink()
            tight = agent.ceiling(model, runs[0][1][1]) + 0.5 * a_request
            with stand_in_api(scripted_buyer()) as (url, seen):
                try:
                    agent.run(few[:1], model, "test-key", tight, url)
                    cut_short = None
                except SystemExit as halt:
                    cut_short = str(halt.code)
                half = agent.kept(model)
                require(cut_short and "cap" in cut_short and seen["requests"] == 1 and not half["answers"]
                        and abs(half["spent"] - a_request) < 1e-15,
                        f"a case the cap cut short does not leave its spending in the saved file: {cut_short}, {half}")
            # A cap with room for a few requests' real cost beside the most one could cost, run until a run asks nothing.
            agent.path(model).unlink()
            small = max(agent.ceiling(model, body) for _, sent, _ in runs[:len(few)] for body in sent) + 3.5 * a_request
            with stand_in_api(scripted_buyer()) as (url, seen):
                capped = []
                while len(capped) < 8 and (len(capped) < 2 or capped[-1][1] != capped[-2][1]):
                    try:
                        agent.run(few, model, "test-key", small, url)
                        capped.append((None, seen["requests"]))
                    except SystemExit as halt:
                        capped.append((str(halt.code), seen["requests"]))
            under = agent.kept(model)
            require(all(said and "cap" in said for said, _ in capped) and capped[-1][1] == capped[-2][1] > 0
                    and abs(under["spent"] - capped[-1][1] * a_request) < 1e-12 and under["spent"] <= small
                    and len(under["answers"]) < len(few),
                    f"runs under a small cap pass it, or lose count of a request: {capped}")
            print(f"  the cap: with room for three and a half requests at the most each could cost, {passed} go out and the next "
                  f"is refused; a second request is held back while a first is still out; a case cut short after one request "
                  f"leaves its ${half['spent']:.6f} in the saved file; {len(capped)} runs capped at ${small:.6f} send "
                  f"{capped[-1][1]} requests between them, spend ${under['spent']:.6f}, and the last sends none")
        finally:
            agent.DATA = home

    # The manual says which commands call the Claude API, so it must name every one that can.
    sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
               for p in [ROOT / "run.py", *sorted((ROOT / "staple").glob("*.py"))]}
    manual = (ROOT / "staple" / "pages" / "manual.html").read_text(encoding="utf-8")
    unnamed = api_senders_unnamed(sources, manual)
    extra = api_senders_unnamed({**sources, "staple/extra.py": "reply = triage.post(body, key)\n"}, manual)
    dropped = api_senders_unnamed(sources, manual.replace(API_COMMANDS["staple/agent.py"], "python -m staple.other"))
    print(f"  {len(API_COMMANDS)} files can send a request to the Claude API, and the manual names the command for each; "
          f"control: a planted file that sends one, and a manual with a command taken out, are each refused")
    require(not unnamed, f"the manual does not name the command for a file that calls the Claude API: {unnamed}")
    require(extra == ["staple/extra.py"] and dropped == ["staple/agent.py"],
            "control: a file that calls the API unnamed, or a manual that drops a command, passes")


API_COMMANDS = {"staple/triage.py": "python run.py --live", "staple/agent.py": "python -m staple.agent"}


def api_senders_unnamed(sources, manual):
    """The files that send a request to the Claude API whose command the manual does not name."""
    senders = [name for name, text in sources.items() if re.search(r"urlopen\(|\btriage\.post\(", text)]
    return sorted(name for name in senders
                  if name not in API_COMMANDS or f"<code>{API_COMMANDS[name]}</code>" not in manual)


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

        ftf = one(a, "SELECT AVG(first_time_fix) FROM jobs")
        parts_caused = one(a, "SELECT AVG(parts_on_truck = 0) FROM jobs")
        print(f"  today's made-up first-time fix {ftf:.1%}; parts missing on {parts_caused:.1%} of visits")
        require(0.4 < ftf < 0.85, "today's first-time fix rate is implausible")
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
