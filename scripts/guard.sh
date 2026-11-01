#!/usr/bin/env bash
# guard.sh — run this repo's regression guards: one test per mistake this repo has already made.
#
# WHY THESE EXIST. Until 2026-09-11 these checks lived as prose entries in ~/.claude/LEDGER.md,
# executed by a nightly audit whose result nothing read. Measured that day: 62 of 403 recorded
# classes had fired AGAIN after being written down, and 190 were reporting the mistake present in
# some tree right now with no consequence anywhere. The checks themselves were sound; the wrapper
# around them was not. They now run here, before a commit, where a failure stops the work.
#
# EVERY TEST IS TWO HALVES AND BOTH MUST HOLD:
#   the control  plants the defect, or proves the search can see its own population
#   the sweep    finds live instances — silence means clean
# A test whose control has gone blind is reported as a FAILURE, not as a pass. That distinction is
# not theoretical: on the first run of this suite one converted check was found printing "CANARY
# FAILED" while the old nightly audit reported it clean, because that audit only ever treated a
# SILENT control as broken.
#
# TWO TIERS, AND THE SPLIT IS A MEASUREMENT RATHER THAN A PREFERENCE.
#   fast/     under the repo's time allowance each; runs before every commit
#   nightly/  a browser render or a full build; too slow for a commit and still worth running
# A gate people wait six minutes for gets bypassed, and a bypassed gate protects nothing.
#
# AND A THIRD, ADDED 2026-09-11, WHICH IS NOT A SLOWER TIER BUT A DIFFERENT KIND OF THING.
#   quarantine/  the check is correct and the defect it finds is REAL and still there
#
# Where it came from. Retiring ~/.claude/LEDGER.md into these suites hit a wall that the plan had
# not priced: of 286 classes still to convert, 192 could not become tests because the mistake each
# one describes is present in the tree RIGHT NOW, and a test written from one would start red. The
# three ways out were to repair all 192 first, to abandon them, or to let the check exist while
# failing. The third was chosen on 2026-09-11 because it is the only one that keeps the check ALIVE
# without blocking anything on repairs nobody has scheduled.
#
# HOW IT BEHAVES, AND EVERY LINE OF THIS IS THE POINT:
#   * it NEVER runs on the commit path. `guard.sh` with no argument is still fast/ alone.
#   * a FAILING quarantined test changes no exit code. That failure is the known debt, and 192
#     permanent reds mixed into the nightly verdict would drown the one new red that matters.
#   * BUT ONLY A FAILURE THAT NAMES ITS DEFECT IS THE DEBT (2026-10-20). One whose control is
#     BLIND, whose scan crashed or checked nothing, or that was killed is a broken check, and it is
#     a FAIL like any other: the debt it was parked on is not being checked at all.
#   * a PASSING quarantined test is the loud event. Somebody fixed the defect, so the test should
#     graduate into fast/ or nightly/ and start defending the repair. Exit code 2 says so.
# A quarantine nobody ever empties is a rubbish bin. The graduation signal is what makes it a queue.
#
# Usage:
#   bash scripts/guard.sh                the fast tier   (npm run guard)
#   bash scripts/guard.sh --nightly      the slow tier   (npm run guard:nightly)
#   bash scripts/guard.sh --quarantine   the known-failing tier; exit 2 if any now PASSES, 1 if any is broken
#   bash scripts/guard.sh --all          all three

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# HOW FAST A FAST TEST IS, IS JUDGED OVERNIGHT, NOT HERE (2026-10-31, his picks). This runner prints
# each test's time and makes no speed verdict, because one run on a busy machine cannot carry one:
# until that day it warned on any run over 2000ms, 370 of 732 runs in the home repository printed
# that warning, and a 14-second test hid in the noise for five days. The nightly planner
# (nightly_plan.py, beside the nightly job) now times each suspect on its own and names one only after
# two confirmations running over its repo's allowance, which a repo may raise in writing in
# scripts/guards/fast-budget ("<milliseconds>  <reason>").

# THE PER-TEST KILL, AND IT IS PER TIER BECAUSE ONE NUMBER WAS MEASURED WRONG (L-374).
# A single 600s bound was added on 2026-09-13 and KILLED A HEALTHY TEST on its first full nightly
# run: a suite executing 34 other suites takes 11m20s on a QUIET machine, so 600s was below its
# legitimate runtime rather than above it. The bound was doing the exact thing its own comment
# warned against — catching slowness instead of hangs, which is how a suite starts being run with
# the guard switched off.
#
# THE TWO TIERS HAVE DIFFERENT LEGITIMATE CEILINGS AND SO NEED DIFFERENT BOUNDS. A fast test lives
# under its repo's time allowance and the commit hook stops the whole suite at the limit on its suite line,
# which this runner reads from the hook below and never copies here. A nightly test can
# run whole suites, and the slowest measured on this estate is 11m20s.
#
# WITHOUT A BOUND, the only thing that notices a test which never returns is whatever kills the
# whole job from outside — and it kills the RUN, not the test. Every result already computed goes
# with it, and the morning report is not "test 14 hung", it is nothing. Measured 2026-09-13: a
# nightly tier ran 705,912ms across 82 tests, so one hang would have cost 81 good results.
GUARD_KILL_FAST="${GUARD_KILL_FAST:-120}"
GUARD_KILL_SLOW="${GUARD_KILL_SLOW:-1800}"
# Seconds between the polite stop at a bound and the forced one, for a test that ignores the first.
GUARD_KILL_GRACE="${GUARD_KILL_GRACE:-5}"
# A GRACE OF 0 SWITCHES THE FORCED STOP OFF, AND SAYS SO (2026-10-04, the review of login-starter
# 9d4a71f): GNU `timeout -k 0` sends no SIGKILL at all, measured with a check that ignores SIGTERM
# running on to its own end.
# THE TEST IS NUMERIC, AND A BOUND OF 0 IS CAUGHT TOO (2026-10-05, reviews C F3, F4, N2 and G F4): the
# old pattern listed 0, 00 and 000, so 0000 still switched the forced stop off, and it read any unit
# as junk, cutting a valid "1m" or "2.5" to 1 second with a line saying it was off. And a bound of 0
# makes `timeout 0s` no bound at all, with nothing said. The grace takes a duration `timeout -k`
# reads (a number, optionally with s, m, h or d); the bounds are plain seconds, as an "s" is appended.
# Each must hold a non-zero digit, and anything else falls back with a line naming the value.
# THE BOUNDS ARE WHOLE SECONDS (2026-10-05, reviews R3 F1 and R10 F1): 11d80196 let a bound of 1.5 or
# 09 through, and the forced-stop branch below multiplies the bound in bash arithmetic, which fails on
# a decimal and reads 09 as a bad octal number. That error ended the whole tier loop: the hung check
# and every check after it went unreported, and the run printed "0 failed" and exited 0. A bound is
# now a whole number of seconds, read in base 10, so 09 is 9; the grace never enters arithmetic and
# keeps timeout's own forms (1m, 2.5). The grace falls back to 1, the smallest that still forces.
guard_duration() { # name value fallback grace|bound -> REPLY
  if [ "$4" = grace ]; then
    if [[ "$2" =~ ^[0-9]+(\.[0-9]+)?[smhd]?$ ]] && [[ "${2%[smhd]}" =~ [1-9] ]]; then REPLY="$2"; return 0; fi
    printf '  (%s=%s is not a duration above zero, and 0 switches it off; using %s)\n' "$1" "$2" "$3"
  else
    # AT MOST SEVEN DIGITS (2026-10-05, reviews R1 F5 and R2 3): bash arithmetic wraps past 2^63, so a
    # twenty-digit bound became 0 or a negative number, and `timeout 0` means no bound at all.
    # 9,999,999 seconds is 115 days, far past any bound worth setting.
    if [[ "$2" =~ ^[0-9]{1,7}$ ]] && [[ "$2" =~ [1-9] ]]; then REPLY=$((10#$2)); return 0; fi
    printf '  (%s=%s is not a whole number of seconds above zero; using %s)\n' "$1" "$2" "$3"
  fi
  REPLY="$3"
}
guard_duration GUARD_KILL_GRACE "$GUARD_KILL_GRACE" 1 grace; GUARD_KILL_GRACE="$REPLY"
guard_duration GUARD_KILL_FAST "$GUARD_KILL_FAST" 120 bound; GUARD_KILL_FAST="$REPLY"
guard_duration GUARD_KILL_SLOW "$GUARD_KILL_SLOW" 1800 bound; GUARD_KILL_SLOW="$REPLY"
case "$GUARD_KILL_GRACE" in *[smhd]) GRACE_SHOWN="$GUARD_KILL_GRACE" ;; *) GRACE_SHOWN="${GUARD_KILL_GRACE}s" ;; esac

tiers="fast"
case "${1:-}" in
  --nightly)    tiers="nightly" ;;
  --quarantine) tiers="quarantine" ;;
  --all)        tiers="fast nightly quarantine" ;;
  "")           ;;
  *) printf 'usage: guard.sh [--nightly|--quarantine|--all]\n' >&2; exit 2 ;;
esac

pass=0; fail=0; total=0; ran=0; absent=''; empty=''
qpass=0; qfail=0; declare -a GRADUATES=()

# --- THE RUN LOG ---------------------------------------------------------------------------------
# WHY IT EXISTS. Until 2026-09-14 this runner printed per-test lines to the terminal and kept
# nothing. That is fine while you are watching. It is useless the moment a failure does not
# reproduce: on 2026-09-14 a commit in claude-home was refused with "39 run, 38 passed, 1 failed",
# the identical retry passed 39/39, and two further full runs passed 39/39 — and WHICH test failed
# was already unrecoverable, because the only copy of that line had scrolled past.
#
# EVERY RUN IS KEPT, NOT JUST THE FAILING ONES. Operator's call, 2026-09-14, and the reason is that
# a run which dies partway through never reaches the point where it would decide it was worth
# saving — so "save only failures" reintroduces the blindness this is here to remove. A green run
# of forty tests costs about 4 KB.
#
# TWO BOUNDS, AND THE AGE ONE BITES FIRST. Lines older than GUARD_LOG_MAX_DAYS go, then if the file
# is still over GUARD_LOG_MAX_BYTES the oldest survivors go until it is not. Every line carries its
# own date as the first field, so trimming is a line filter and can never leave half a record. All
# lines of one run share the run's start date, so a run is never half-trimmed.
#
# IT IS OUTSIDE EVERY REPO, keyed by repo name, so no repo needs a .gitignore line for it and two
# repos cannot overwrite each other's history.
GUARD_LOG_MAX_BYTES="${GUARD_LOG_MAX_BYTES:-4194304}"   # 4 MB, roughly 1,000 runs of forty tests
GUARD_LOG_MAX_DAYS="${GUARD_LOG_MAX_DAYS:-14}"
if [ -z "${GUARD_LOG+x}" ]; then
  _glog_repo="$(basename "$(cd "$HERE/.." 2>/dev/null && pwd)" 2>/dev/null)"
  GUARD_LOG="${HOME}/.claude/cache/guard-runs-${_glog_repo:-unknown}.log"
fi
printf -v _GLOG_DAY '%(%Y-%m-%d)T' -1
printf -v _GLOG_AT  '%(%H:%M:%S)T' -1
_GLOG_RUN="$$"

# WARN, NEVER FAIL SILENTLY. A log that cannot be written says so once on stderr and the run
# continues — a runner that dies because its diary is unwritable is worse than no diary.
if [ -n "$GUARD_LOG" ]; then
  if ! mkdir -p "$(dirname "$GUARD_LOG")" 2>/dev/null; then
    printf 'guard.sh: cannot create %s — this run is NOT being recorded.\n' "$(dirname "$GUARD_LOG")" >&2
    GUARD_LOG=''
  fi
fi

_glog() { [ -n "$GUARD_LOG" ] || return 0
          printf '%s %s %s %s\n' "$_GLOG_DAY" "$_GLOG_AT" "$_GLOG_RUN" "$*" >> "$GUARD_LOG" 2>/dev/null || true; }

# One line per test, plus the captured output when it did not pass. The label mirrors the terminal
# exactly, so a reader of the log and a reader of the screen are reading the same verdict.
#
# A PARKED CHECK'S FAILURE IS ITS KNOWN DEFECT ONLY WHEN IT SAYS SO (2026-10-20). Every parked check
# prints "this mistake is present" over what it found. Until that day any failure counted as the
# debt, so one whose control had gone BLIND read as its defect still there, and two did for weeks.
# A finding that says nothing was checked is a crashed scan's, not the defect's. The nightly job
# carries the same rule, and its test runs both on the same three outputs.
guard_parked_found() {  # <output> -> 0 when it names the defect it was parked on
  case "$1" in *"nothing was checked"*) return 1 ;; *"this mistake is present"*) return 0 ;; esac
  return 1
}
_glog_test() {  # <tier> <rc> <ms> <name> <output>
  [ -n "$GUARD_LOG" ] || return 0
  local _t="$1" _rc="$2" _ms="$3" _n="$4" _o="$5" _label
  if [ "$_t" = quarantine ] && [ "$_rc" -eq 0 ]; then _label=PASSES
  elif [ "$_t" = quarantine ] && [ "$_rc" -ne 124 ] && guard_parked_found "$_o"; then _label=known
  elif [ "$_rc" -eq 0 ]; then _label=ok
  elif [ "$_rc" -eq 124 ]; then _label=KILLED
  else _label=FAIL
  fi
  _glog "$(printf '%-6s %6sms  %s/%s' "$_label" "$_ms" "$_t" "$_n")"
  case "$_label" in
    FAIL|KILLED|PASSES) printf '%s\n' "$_o" | while IFS= read -r _l; do _glog "    | $_l"; done ;;
  esac
}

# TRIMMED AT MOST ONCE A DAY UNLESS THE SIZE BOUND DEMANDS IT SOONER, because this runs on the
# commit path and an unconditional pass over a 4 MB file at every commit is a cost with no payer.
_glog_trim() {
  [ -n "$GUARD_LOG" ] && [ -s "$GUARD_LOG" ] || return 0
  local _stamp='' _bytes _cut _tmp
  [ -f "$GUARD_LOG.trimmed" ] && read -r _stamp < "$GUARD_LOG.trimmed" 2>/dev/null
  _bytes=$(wc -c < "$GUARD_LOG" 2>/dev/null) || _bytes=0
  [ "$_stamp" = "$_GLOG_DAY" ] && [ "${_bytes:-0}" -le "$GUARD_LOG_MAX_BYTES" ] && return 0
  _cut=$(date -d "-${GUARD_LOG_MAX_DAYS} days" +%Y-%m-%d 2>/dev/null) || _cut=''
  _tmp="$GUARD_LOG.tmp.$$"
  if [ -n "$_cut" ]; then
    awk -v c="$_cut" 'substr($0,1,10) >= c' "$GUARD_LOG" > "$_tmp" 2>/dev/null || { rm -f "$_tmp"; return 0; }
  else
    cp "$GUARD_LOG" "$_tmp" 2>/dev/null || { rm -f "$_tmp"; return 0; }
  fi
  # THE SIZE BOUND IS SECOND AND IT CUTS ON A LINE BOUNDARY. `tail -c` alone would leave a partial
  # first line whose date field is truncated, which the age filter above would then read as a date
  # that sorts wrong forever; dropping that first line is what makes the two bounds composable.
  if [ "$(wc -c < "$_tmp" 2>/dev/null || echo 0)" -gt "$GUARD_LOG_MAX_BYTES" ]; then
    tail -c "$GUARD_LOG_MAX_BYTES" "$_tmp" | tail -n +2 > "$_tmp.2" 2>/dev/null && mv -f "$_tmp.2" "$_tmp"
  fi
  mv -f "$_tmp" "$GUARD_LOG" 2>/dev/null && printf '%s\n' "$_GLOG_DAY" > "$GUARD_LOG.trimmed" 2>/dev/null
  rm -f "$_tmp" "$_tmp.2" 2>/dev/null || true
}

_glog_trim
_glog "=== run start  tiers:$tiers  repo:$(cd "$HERE/.." 2>/dev/null && pwd)"

# EVERY PROCESS A CHECK STARTS CARRIES THIS RUN'S MARK, so the sweep after the loop can find what the
# group kill missed: a child that leads a group of its own, or one created in the instant the group
# was killed. A runner started inside a check sets its own mark and sweeps its own children.
GUARD_RUN_TOKEN="guard-run-$$-${RANDOM}${RANDOM}"; export GUARD_RUN_TOKEN

# --- AT COMMIT, A FAST TEST RUNS WHEN THE COMMIT TOUCHES WHAT IT COVERS (2026-10-06) -------------
# WHY. Every fix adds a test, and every fast test ran before every commit, so the commit wait grew
# with the history rather than with the change: claude-home's fast tier was cut to 32 tests on 10-01
# and stood at 43 five days later, a median 42.5s per commit, with 30 commits killed at 150s in 60
# days. The operator ticked this on 2026-10-06 (estate lean-out, C1), having twice declined moving
# tests to the nightly tier, because a test moved there stops guarding the commit that breaks it.
# This keeps that guard: a test still runs before any commit that touches its subject.
#
# HOW. githooks/pre-commit sets GUARD_SCOPE=staged. A fast test whose first 60 lines hold
#   # COVERS: <glob> <glob> ...
# (globs relative to the repo root; * crosses folders) runs only when a staged path matches one of
# them, or when the test file itself is staged. A test with NO COVERS line always runs, so a test
# nobody has described is never skipped. Without GUARD_SCOPE (a hand run, the nightly run) every
# test runs, and the nightly runner runs this fast tier every night, so a skipped test is never
# more than a day from running.
# Builtins only: a `head` per test would cost ~40ms each on this machine.
_sel_on=0; declare -a _sel_staged=(); skipped=0
if [ "${GUARD_SCOPE:-}" = staged ]; then
  _sel_on=1
  mapfile -t _sel_staged < <(git -C "$HERE/.." diff --cached --name-only 2>/dev/null | tr -d '\r')
fi
_sel_wanted() {  # <test file> <tier> -> 0 to run it, 1 to skip it
  [ "$_sel_on" = 1 ] || return 0
  local t="$1" rel="scripts/guards/$2/${1##*/}" line covers="" n=0 p g
  local -a globs=()
  while IFS= read -r line && [ "$n" -lt 60 ]; do
    n=$((n + 1))
    case "$line" in '# COVERS:'*) covers=${line#'# COVERS:'}; break ;; esac
  done < "$t"
  covers=${covers%$'\r'}
  [ -n "${covers//[[:space:]]/}" ] || return 0
  read -r -a globs <<< "$covers"
  for p in "${_sel_staged[@]}"; do
    [ "$p" = "$rel" ] && return 0
    for g in "${globs[@]}"; do
      # shellcheck disable=SC2053  # $g IS a pattern, on purpose
      [[ $p == $g ]] && return 0
    done
  done
  return 1
}

# A CHECK WITH TWO COVERS LINES IS REFUSED UNRUN, in every mode (2026-10-23, his call on the ask of
# 2026-10-09). _sel_wanted above reads only the first `# COVERS:` line, so a check written with one
# line per file ran at commit only when its first line's files were staged, and nothing said so: four
# guards were written that way on 2026-10-09, and house-node's was skipped on a commit that changed the
# very file it checks. One COVERS line holds every glob. Kept apart from _sel_wanted, which a check cuts
# out of this file and runs alone. _cov_n is how many COVERS lines the first 60 lines hold.
_cov_count() {  # <test file> -> sets _cov_n
  local line n=0; _cov_n=0
  while IFS= read -r line && [ "$n" -lt 60 ]; do
    n=$((n + 1))
    case "$line" in '# COVERS:'*) _cov_n=$((_cov_n + 1)) ;; esac
  done < "$1"
}
_fast_ran=0; _fast_ms=0; _always_ms=0

for tier in $tiers; do
  dir="$HERE/guards/$tier"
  # A TIER THAT DOES NOT EXIST AND A TIER THAT EXISTS AND IS EMPTY ARE DIFFERENT EVENTS, and until
  # 2026-09-11 this runner treated both as nothing to report. A repo that has never used the slow
  # tier is fine and says so. A tier DIRECTORY holding no tests is a suite whose guards were removed
  # or whose glob stopped matching, and it reports "0 failed" — which reads exactly like a clean
  # run. Measured that day: six of eleven repos reported clean having executed nothing at all.
  if [ ! -d "$dir" ]; then absent="$absent $tier"; continue; fi
  printf '\n%s tier:\n' "$tier"
  tier_ran=0
  for t in "$dir"/*.sh; do
    [ -e "$t" ] || continue
    # A SKIPPED TEST STILL COUNTS AS PRESENT for the empty-tier test below, and is counted on its own
    # line in the summary, so "N run" never quietly shrinks without saying how many waited.
    # A refused check takes the same FAIL path as a failing one, so the runner prints each result
    # kind from one line, which login-starter's build check reads.
    _cov_n=0; _refused=""
    if [ "$tier" = fast ]; then
      _cov_count "$t"
      [ "$_cov_n" -gt 1 ] && _refused="refused unrun: it holds $_cov_n \`# COVERS:\` lines and only the first is read, so a commit
touching only the files on the others would skip it. Put every glob on one COVERS line."
    fi
    if [ "$tier" = fast ] && [ -z "$_refused" ] && ! _sel_wanted "$t" "$tier"; then
      tier_ran=$((tier_ran+1)); skipped=$((skipped+1))
      continue
    fi
    # QUARANTINED TESTS ARE NOT IN `ran`, so "N test(s) run, P passed, F failed" still adds up.
    # They are counted on their own line below, which is the whole reason the tier exists.
    [ "$tier" = quarantine ] || ran=$((ran+1))
    tier_ran=$((tier_ran+1))
    # BOTH BOUNDS ARE HANG DETECTORS AND NEITHER IS A SPEED LIMIT, which is why each sits far above
    # any legitimate runtime for its tier. Speed has its own instrument: the nightly run times a
    # suspect on its own and NAMES a fast test that has grown slow, without killing it. A bound tight enough to catch
    # slowness would eventually kill a legitimate slow test, which is how a suite starts being run
    # with the guard switched off — and that is not hypothetical, it is what the single 600s bound
    # did on its first nightly run before it was split into the pair above.
    #
    # THE FALLBACK KEEPS THIS WORKING WHERE `timeout` IS ABSENT, because a runner that dies on a
    # missing tool is worse than an unbounded one.
    #
    # THE OUTPUT GOES TO A FILE, AND A FORCED STOP FOLLOWS THE POLITE ONE (2026-10-04, the review of
    # skills 4d5e6eb). Read through `$(...)`, the runner waited for every process holding the pipe, so a
    # check that left a background child running held the run for as long as that child lived, past
    # any bound. And `timeout` alone sends SIGTERM, which a check can ignore and run on to its own
    # end; `-k` sends SIGKILL GUARD_KILL_GRACE seconds later, and `timeout` then exits 137, not 124.
    # WHAT A PASSING CHECK LEAVES BEHIND IS KILLED WITH IT, AND QUIETLY (2026-10-04, found by review B
    # of claude-home 1450c2c4, fixed in dc22d0aa): with the output in a file nothing waited on a background child, so one a check left
    # running outlived the whole run; and a forced stop made bash print "Killed" for the job, naming no
    # check. `timeout` leads its own process group, so the group is killed once the check returns, and
    # the subshell absorbs the job notice.
    # TWO KINDS OF CHILD ESCAPE THAT KILL, and the sweep after the loop below catches both (2026-10-05,
    # reviews R3 F2, R10 F2 and R2 2): one still being created when the group is killed, which survived
    # in 1 to 4 runs of 10, and one that leads a group of its own, such as `timeout 20 bash -c ... &`.
    _kill=""; _cap="$(mktemp 2>/dev/null)" || _cap=""
    [ -n "$_cap" ] || printf '  (no temp file: %s is read through a pipe, which a child it leaves running can hold open)\n' "$(basename "$t")"
    # THE CLOCK BRACKETS THE CHECK ALONE (2026-10-04, the review of login-starter 9d4a71f): started
    # before mktemp and stopped after cat and rm, it charged each check about 100ms of the runner's own
    # work, measured 202-213ms against 102-106ms, which tipped checks near the budget over it. The
    # subshell that launches the check adds about 15ms of its own on a quiet machine (100 against 115ms
    # for a trivial check, measured 2026-10-05); under heavy load it measured several hundred.
    s=$(date +%s%3N)
    if [ -n "$_refused" ]; then
      rc=1
    elif command -v timeout >/dev/null 2>&1; then
      if [ "$tier" = fast ]; then _kill="$GUARD_KILL_FAST"; else _kill="$GUARD_KILL_SLOW"; fi
      if [ -n "$_cap" ]; then
        ( timeout -k "$GUARD_KILL_GRACE" "${_kill}s" bash "$t" > "$_cap" 2>&1 & _tp=$!
          wait "$_tp"; _trc=$?
          kill -KILL -- "-$_tp" 2>/dev/null
          exit "$_trc" ) 2>/dev/null; rc=$?
      else out="$(timeout -k "$GUARD_KILL_GRACE" "${_kill}s" bash "$t" 2>&1)"; rc=$?; fi
    else
      if [ -n "$_cap" ]; then bash "$t" > "$_cap" 2>&1; rc=$?; else out="$(bash "$t" 2>&1)"; rc=$?; fi
    fi
    ms=$(( $(date +%s%3N) - s )); total=$((total+ms))
    if [ "$tier" = fast ] && [ -z "$_refused" ]; then
      _fast_ran=$((_fast_ran+1)); _fast_ms=$((_fast_ms+ms))
      [ "$_cov_n" -eq 0 ] && _always_ms=$((_always_ms+ms))
    fi
    if [ -n "$_cap" ]; then out="$(cat "$_cap" 2>/dev/null)"; rm -f "$_cap" 2>/dev/null; fi
    [ -n "$_refused" ] && out="$_refused"
    # A forced stop at the bound is the same event as a polite one, so it reads as 124 below.
    [ "$rc" -eq 137 ] && [ -n "$_kill" ] && [ "$ms" -ge $(( _kill * 1000 )) ] && rc=124
    name="$(basename "$t" .sh)"
    # RECORDED BEFORE THE BRANCHES BELOW, not inside them. Those arms end in `continue` and each
    # prints its own wording, so a log call per arm is several chances to add a new arm later and
    # forget one. One call here cannot be missed by a branch that does not exist yet.
    _glog_test "$tier" "$rc" "$ms" "$name" "$out"
    # THE QUARANTINE TIER READS ITS RESULTS THE OTHER WAY UP. A failure there is the debt being
    # confirmed, which is expected and carries no consequence; a PASS is news, because it means the
    # defect the test was parked on has been fixed and the test should now be defending that fix.
    if [ "$tier" = quarantine ]; then
      if [ "$rc" -eq 0 ]; then
        qpass=$((qpass+1)); GRADUATES+=("$name")
        printf '  PASSES %5dms  %s\n' "$ms" "$name"
        printf '         ^ the defect this was parked on is GONE. Move it to fast/ or nightly/.\n'
        continue
      elif [ "$rc" -ne 124 ] && guard_parked_found "$out"; then
        qfail=$((qfail+1)); printf '  known %6dms  %s\n' "$ms" "$name"
        continue
      fi
      # Anything else is a broken check, run and failed like any other (guard_parked_found).
      ran=$((ran+1))
    fi
    if [ "$rc" -eq 0 ]; then
      pass=$((pass+1)); printf '  ok    %6dms  %s\n' "$ms" "$name"
    elif [ "$rc" -eq 124 ]; then
      # KILLED IS NOT FAILED, and 124 is `timeout`'s own exit code for a command it stopped. It
      # counts against the suite exactly like a failure — a test that did not finish proves
      # nothing — but it is NAMED differently, because the fix is different. A failing test has
      # FOUND something; a killed one has HUNG, and the output below is whatever it managed first.
      fail=$((fail+1)); printf '  KILLED %5dms  %s\n' "$ms" "$name"
      printf '         ^ hit the %ss per-test bound for the %s tier; one that ignores the polite stop runs up\n' "$_kill" "$tier"
      printf '           to %s more before the forced one, so its time can pass the bound. Every other test in this run\n' "$GRACE_SHOWN"
      printf '           still reported, which is the whole point of the bound (L-374).\n'
      printf '%s\n' "$out" | sed 's/^/          /'
    else
      fail=$((fail+1)); printf '  FAIL  %6dms  %s\n' "$ms" "$name"
      [ "$tier" = quarantine ] && printf '        ^ parked, but this is not its defect being found: the check itself is broken, and\n          nothing is checking the debt it was parked on. Repair it where it stands.\n'
      printf '%s\n' "$out" | sed 's/^/          /'
    fi
  done
  # AN EMPTY QUARANTINE IS THE GOAL, NOT A FAULT. Every other tier holding no tests means its
  # guards went missing; this one holding none means the debt is paid.
  [ "$tier_ran" -eq 0 ] && [ "$tier" != quarantine ] && empty="$empty $tier"
done

# THE SWEEP: anything still carrying this run's mark outlived its check, so it is stopped and named.
# The runner carries the mark too and is skipped. Measured 2026-10-05: one grep over /proc, 68ms, and
# it found and stopped both `timeout` and its child where the review's group-led orphan had escaped.
# ponytail: a Windows program a check starts in the background shows no environment under /proc, so
# it is not found here. Upgrade path: a Windows job object that the runner's checks start inside.
_left=""
for _f in $(grep -l -a "GUARD_RUN_TOKEN=$GUARD_RUN_TOKEN" /proc/[0-9]*/environ 2>/dev/null); do
  _p="${_f#/proc/}"; _p="${_p%/environ}"
  [ "$_p" = "$$" ] && continue
  [ -r "/proc/$_p/cmdline" ] || continue
  kill -KILL "$_p" 2>/dev/null && _left="$_left $_p"
done
[ -n "$_left" ] && printf '\n  (a check left process(es) running past its end; stopped:%s)\n' "$_left"

printf '\n%d test(s) run, %d passed, %d failed, %dms total.\n' "$ran" "$pass" "$fail" "$total"
[ "$skipped" -gt 0 ] && printf '%d fast test(s) skipped: they cover nothing this commit stages, and the nightly run runs them.\n' "$skipped"
# THE SUMMARY IS THE LAST THING WRITTEN, so a record with no "run end" line is a run that died
# partway — which is a fact worth reading, and the one a save-only-on-failure design cannot record.
_glog "$(printf '=== run end    %d run, %d passed, %d failed, %dms total' "$ran" "$pass" "$fail" "$total")"

# THE QUARANTINE IS COUNTED ON ITS OWN LINE, never folded into the numbers above. Folding a known
# failure into "failed" is how a board of 192 expected reds hides the one unexpected one.
if [ "$(( qpass + qfail ))" -gt 0 ]; then
  printf 'quarantine: %d still failing as expected, %d now PASSING.\n' "$qfail" "$qpass"
fi
if [ "${#GRADUATES[@]}" -gt 0 ]; then
  printf 'READY TO GRADUATE — the defect each of these was parked on is fixed:\n'
  printf '  %s\n' "${GRADUATES[@]}"
  printf 'Move each into fast/ or nightly/ so it starts defending the repair.\n'
fi

# SAID, NEVER SILENT. A tier this repo does not use is a fact worth one line; it is not a failure,
# because a repo that has no slow tests yet is in a normal state and blocking its commits over that
# would make the runner the problem.
[ -n "$absent" ] && printf 'no%s tier in this repo — nothing to run there.\n' "$absent" >&2

# AND A TIER THAT EXISTS AND RAN NOTHING IS A FAILURE. Its tests were removed, or the glob stopped
# matching, and the summary line above it says "0 failed" in exactly the tone a clean run uses.
# Precedent: the same reasoning ledger-audit.sh carries about printing `0 entries — 0 clean`.
if [ -n "$empty" ]; then
  printf 'GUARD SUITE RAN NOTHING in the%s tier, which EXISTS and holds no tests.\n' "$empty" >&2
  printf '  That is a failure, not a pass. Check scripts/guards/ for a directory emptied by accident.\n' >&2
  exit 1
fi

# THE SUITE'S TOTAL, NOT JUST EACH TEST'S. Added 2026-09-11, and the per-test allowance the nightly
# run judges does not cover this: forty tests can each sit comfortably under 2000ms while their SUM walks into the
# commit hook's kill. Past that the suite is killed, the commit is refused, and NOTHING WAS
# CHECKED — the worst of both. Measured the day this was added: claude-home's fast tier reached 38
# tests and 23.5s mid-conversion, 39% of the kill, with more still to convert. A warning at 60%
# leaves room to scope the costliest tests with COVERS lines before the gate starts refusing work.
#
# THE NUMBER IS READ OUT OF THE HOOK THAT ENFORCES IT AND IS WRITTEN DOWN NOWHERE ELSE HERE, from
# 2026-09-17. This block used to carry `${GUARD_HOOK_KILL_MS:-60000}` and a comment stating the
# kill was 60s. NOTHING ANYWHERE SET THAT VARIABLE, so the fallback was always what ran — and the
# hook was raised to 90s on 2026-09-14. Every one of the eighteen copies of this runner therefore
# warned at 36s against a real ceiling of 90s, and said 60 in prose beside it. A duplicated number
# does not announce that it has drifted; it just quietly reports the wrong percentage.
#
# NO GUESS AT ALL, since 2026-10-23 (box item 37). An unreadable hook used to fall back to 60000 and warn
# against it, still the 60s the hook had dropped on 2026-09-14. Now nothing is measured and that is said.
#
# JUDGED AGAINST WHAT A COMMIT RUNS, since 2026-10-23 (box item 32, his pick). A commit
# (GUARD_SCOPE=staged) runs the fast tests covering what it stages plus every one with no COVERS line,
# so its own fast total is what the stop sees. A hand run runs every fast test, which no commit does: on
# 2026-10-20 one warned at 214s of a 150s stop while the widest commit that day ran 63 tests in 82s.
# For a hand run the number that bounds every commit is the tests with no COVERS line, which run on all
# of them. The advice to move tests to nightly/ is gone: he declined it twice, since a test moved there
# stops guarding the commit that breaks it.
if [ "$_fast_ran" -gt 0 ]; then
  KILL_MS="${GUARD_HOOK_KILL_MS:-}"
  if [ -z "$KILL_MS" ]; then
    _gr_hook="${GUARD_HOOK_PATH:-$HOME/.claude/githooks/pre-commit}"
    # ANCHORED ON THE LINE THAT RUNS THE SUITE, AND THAT ANCHOR IS THE WHOLE CORRECTNESS OF THIS.
    # The hook carries TWO `timeout -k` lines: the gate's own 20s on estate-check.sh, and the
    # suite's kill further down. A first-match extraction returns 20 and the warning would fire at
    # 12s — worse than the 60000 it replaced, and confidently so. Measured 2026-09-17: the naive
    # form returned 20, this one returns 90.
    _gr_secs="$(sed -n 's|.*timeout -k [0-9]*s \([0-9]*\)s .*scripts/guard\.sh.*|\1|p' "$_gr_hook" 2>/dev/null | head -1)"
    # A value outside this range means the pattern matched something that is not a timeout.
    case "$_gr_secs" in
      ''|*[!0-9]*) _gr_secs="" ;;
      *) [ "$_gr_secs" -lt 10 ] || [ "$_gr_secs" -gt 900 ] && _gr_secs="" ;;
    esac
    if [ -n "$_gr_secs" ]; then
      KILL_MS="${_gr_secs}000"
    else
      printf 'NOTE: the commit stop could not be read from %s, so this run was not measured\n' "$_gr_hook" >&2
      printf '  against it. Read the suite line in that file for the real limit.\n' >&2
    fi
  fi
  if [ -n "$KILL_MS" ]; then
    if [ "${GUARD_SCOPE:-}" = staged ]; then
      _judged=$_fast_ms; _what="this commit's fast tests"
    else
      _judged=$_always_ms; _what="the fast tests with no COVERS line, which every commit runs"
    fi
    if [ "$_judged" -gt $(( KILL_MS * 60 / 100 )) ]; then
      printf 'THIS SUITE IS %dms for %s, %d%% of the %dms the commit hook stops at.\n' \
        "$_judged" "$_what" $(( _judged * 100 / KILL_MS )) "$KILL_MS" >&2
      printf '  Past that stop the commit is refused AND nothing was checked. A COVERS line naming the\n' >&2
      printf '  files a test checks lets every commit that touches none of them skip it.\n' >&2
    fi
  fi
fi

# A REAL FAILURE OUTRANKS A GRADUATION, because one is something broken and the other is something
# fixed. Exit 2 is reserved for "nothing is broken AND there is quarantined work ready to move",
# which is the only state a caller needs to tell apart from a plain clean run.
[ "$fail" -eq 0 ] || exit 1
[ "$qpass" -eq 0 ] || exit 2
exit 0
