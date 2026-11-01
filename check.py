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
