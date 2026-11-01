"""Every check for Staple. `python check.py` runs them all; `python check.py <name>` runs one.

Each check prints what it measured and exits non-zero on failure. The controls exist to prove a
check can still fail: a check that never fails looks exactly like one that sees nothing.
"""
import ast
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
        require(jobs == 45_000, f"expected 45,000 jobs, found {jobs}")
        require(0.07 < test / jobs < 0.10, "the test split is not the last three months' share")
        require(0.07 < mis < 0.11 and 0.065 < rnd < 0.095, "planted noise is off its intended rate")

        wrong_type = one(a, """SELECT COUNT(*) FROM job_parts jp JOIN jobs j USING (job)
                               JOIN parts p USING (part) WHERE p.type != j.type""")
        require(wrong_type == 0, f"{wrong_type} job parts belong to another appliance type")

        locked_bad = one(a, """SELECT COUNT(*) FROM truck_stock s JOIN parts p USING (part)
                               JOIN trucks t USING (truck) JOIN techs h USING (tech)
                               WHERE p.locked = 1 AND h.certified = 0""")
        require(locked_bad == 0, f"{locked_bad} locked parts sit on uncertified trucks")

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
