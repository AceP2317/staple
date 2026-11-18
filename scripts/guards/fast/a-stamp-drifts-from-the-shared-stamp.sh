#!/usr/bin/env bash
# A stamp drifts from the shared stamp
#
# WHY THIS TEST EXISTS
# Every build the author makes shows the same build stamp, written by one shared Node script. On
# 2026-11-10 this repository stopped calling that script and began reading its stamp from git in Python,
# because a stranger's clone has no copy of the script and its pages came out unstamped. That left two
# copies of one rule, and two copies drift apart with nothing saying so.
#
# WHAT IT DOES. Builds a scratch history where both stamps must agree, then compares what each reports:
# the build number, the day, the change's short code and the mark for unsaved work. The two write the day
# in different orders on purpose, so the day is compared as a date, never as text. It runs on every commit,
# because the shared script changes outside this repository and no file here says when.
#
# THE SHARED SCRIPT IS NOT IN THIS REPOSITORY. A one-line file in the private folder says where it is. A
# copy with no such file fails here and says nothing was checked, because a pass that compared nothing
# proves nothing.
#
# WHAT IT CANNOT SEE. Here the stamp names the last change outside docs/, so committing the published copy
# leaves it alone. The shared script names the newest change. The scratch history never holds a docs/
# change, so that difference, which is deliberate, is never compared.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
WHERE="$REPO_ROOT/.private/shared-stamp"
if [ ! -f "$WHERE" ]; then
  printf 'FAIL this copy has no .private/shared-stamp naming the shared stamp script, so nothing was checked\n'
  exit 1
fi
SHARED="$(head -n 1 "$WHERE" | tr -d '\r')"; SHARED="${SHARED/#\~/$HOME}"
if [ ! -f "$SHARED" ]; then
  printf 'FAIL the shared stamp script is not at %s, so nothing was checked\n' "$SHARED"
  exit 1
fi

# A scratch repository must not inherit the committing git's index, or it writes into the commit being made.
unset $(git rev-parse --local-env-vars)
# The stamps carry a middle dot, so every Python here reads and writes UTF-8 whatever the console's code page.
export PYTHONUTF8=1
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DAY="$(node -e "console.log(new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date()))")"
[ -n "$DAY" ] || { printf 'FAIL Node did not print the day, so nothing was checked\n'; exit 1; }
SCRATCH="$TMP/history"
git init -q "$SCRATCH" || { printf 'FAIL the scratch history could not be made, so nothing was checked\n'; exit 1; }
save() {  # $1 = a file to change and commit, at noon on the day the shared script will report
  printf '%s\n' "$1" >> "$SCRATCH/$1"
  git -C "$SCRATCH" -c core.hooksPath="$SCRATCH/.git/nohooks" -c core.autocrlf=false add "$1" &&
  GIT_AUTHOR_DATE="${DAY}T12:00:00-05:00" GIT_COMMITTER_DATE="${DAY}T12:00:00-05:00" \
    git -C "$SCRATCH" -c core.hooksPath="$SCRATCH/.git/nohooks" -c user.name=scratch -c user.email=scratch@example.invalid \
    commit -q -m "$1"
}
save first.txt && save second.txt || { printf 'FAIL the scratch history could not be committed, so nothing was checked\n'; exit 1; }

both() {  # prints the shared stamp, then this repository's, for the scratch history as it stands
  ( cd "$SCRATCH" && node "$SHARED" 2>/dev/null )
  python -c "import sys; sys.path.insert(0, sys.argv[1]); from staple import report; print(report.stamp(sys.argv[2])[0])" "$REPO_ROOT" "$SCRATCH"
}
CLEAN="$(both)"
printf 'more\n' >> "$SCRATCH/first.txt"
UNSAVED="$(both)"

python - "$CLEAN" "$UNSAVED" <<'EOF'
import re
import sys

MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
SHARED = re.compile(rf"^(?:Build (\d+) · )?(\d{{1,2}}) ({MONTHS}) (\d{{4}}) · ([0-9a-f]{{7,12}})(\+?)$")
OURS = re.compile(rf"^(?:Build (\d+) · )?({MONTHS}) (\d{{1,2}}), (\d{{4}}) · ([0-9a-f]{{7,12}})(\+?)$")
MONTH = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
FIELDS = ("build number", "day", "short code", "unsaved mark")


def read(line, shape, day_first):
    m = shape.match(line.strip())
    if not m:
        return None
    number, a, b, year, code, plus = m.groups()
    day, month = (a, b) if day_first else (b, a)
    return number, f"{year}-{MONTH[month]:02d}-{int(day):02d}", code, plus


def differences(pair):
    lines = pair.splitlines()
    if len(lines) != 2:
        return [f"expected two stamps and got {len(lines)}: {lines}"]
    shared, ours = read(lines[0], SHARED, True), read(lines[1], OURS, False)
    if shared is None:
        return [f"the shared stamp '{lines[0]}' is not in the shape this test reads"]
    if ours is None:
        return [f"this repository's stamp '{lines[1]}' is not in the shape this test reads"]
    return [f"the {f} differs: shared '{s}', here '{o}'" for f, s, o in zip(FIELDS, shared, ours) if s != o]


clean, unsaved = sys.argv[1], sys.argv[2]
# THE CONTROL: a stamp off by one build, and one that lost its unsaved mark, must each be refused, or a
# clean comparison below would prove nothing.
first, second = clean.splitlines()
planted = {
    "a build number off by one": f"{first}\n{re.sub(r'Build (\d+)', lambda m: f'Build {int(m.group(1)) + 1}', second)}",
    "a lost unsaved mark": "\n".join(l.rstrip("+") if i else l for i, l in enumerate(unsaved.splitlines())),
}
for name, pair in planted.items():
    if not differences(pair):
        print(f"FAIL the check is blind: {name} was not refused, so a clean comparison would prove nothing")
        sys.exit(1)
if not unsaved.splitlines()[0].endswith("+"):
    print("FAIL the shared stamp did not mark unsaved work in the scratch history, so the unsaved case proved nothing")
    sys.exit(1)

found = [f"with nothing unsaved, {d}" for d in differences(clean)] + [f"with work unsaved, {d}" for d in differences(unsaved)]
if found:
    print("FAIL this repository's stamp no longer matches the shared stamp:")
    for d in found:
        print(f"  {d}")
    sys.exit(1)
print(f"ok both stamps agree on '{clean.splitlines()[0]}' and with work unsaved, after {len(planted)} planted drifts were refused")
EOF
