#!/usr/bin/env bash
# Runs this repository's guards. A guard is a small script in scripts/guards/ that fails when a mistake made
# here once comes back, and each first plants that mistake to prove it can still see it.
#
#   bash scripts/guard.sh                the fast guards, which run before each commit
#   bash scripts/guard.sh --nightly      the slow guards, in scripts/guards/nightly/
#   bash scripts/guard.sh --quarantine   guards parked in scripts/guards/quarantine/ until what they find is fixed
#   bash scripts/guard.sh --all          all three
#
# It exits 0 when every guard passed. It exits 1 when a guard failed or was stopped at its time limit, or when
# a folder of guards holds none. It exits 2 for an argument it does not know, and when nothing failed and a
# parked guard now passes, which means that guard should go back to work.
#
# A commit hook may set GUARD_SCOPE=staged. A fast guard that says what it covers, on a line opening
# "# COVERS:" and listing file patterns, then runs only when the commit holds a matching file or the guard itself.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  "")           tiers="fast" ;;
  --nightly)    tiers="nightly" ;;
  --quarantine) tiers="quarantine" ;;
  --all)        tiers="fast nightly quarantine" ;;
  *) printf 'usage: guard.sh [--nightly|--quarantine|--all]\n' >&2; exit 2 ;;
esac

# Seconds before a guard that hangs is stopped. Both sit far above an honest run, so they catch a hang and
# never a slow day.
FAST_LIMIT="${GUARD_KILL_FAST:-120}"
SLOW_LIMIT="${GUARD_KILL_SLOW:-1800}"
# The stop needs GNU timeout. Windows ships another program by that name, so the one beside bash is tried first.
TIMEOUT=""
for candidate in /usr/bin/timeout "$(command -v timeout 2>/dev/null)"; do
  if [ -n "$candidate" ] && "$candidate" --version 2>/dev/null | grep -q coreutils; then TIMEOUT="$candidate"; break; fi
done
[ -n "$TIMEOUT" ] || printf '(no GNU timeout on this machine, so a guard that hangs is not stopped)\n' >&2
# ponytail: a guard that leaves a program running in the background is not cleaned up after. The upgrade is to
# start each guard in a process group of its own and stop the whole group when the guard returns.

# The milliseconds a fast guard is allowed, with the reason beside it in the same file. A slower run is noted
# and never failed, because one run on a busy machine cannot show that a guard has grown slow.
ALLOWED=""
[ -f "$HERE/guards/fast-budget" ] && read -r ALLOWED _ < "$HERE/guards/fast-budget"
case "$ALLOWED" in ''|*[!0-9]*) ALLOWED="" ;; esac

now() {  # the time in milliseconds, or in whole seconds where this machine's date counts no finer
  local t; t="$(date +%s%3N)"
  case "$t" in *[!0-9]*) t="$(date +%s)000" ;; esac
  printf '%s' "$t"
}

staged=""
[ "${GUARD_SCOPE:-}" = staged ] && staged="$(git -C "$HERE/.." diff --cached --name-only | tr -d '\r')"

wanted() {  # <guard file> <tier>: whether this run starts it
  [ "${GUARD_SCOPE:-}" = staged ] && [ "$2" = fast ] || return 0
  local covers path pattern
  local -a patterns
  covers="$(sed -n '1,60s/^# COVERS://p' "$1" | tr -d '\r' | tr '\n' ' ')"
  [ -n "${covers// /}" ] || return 0    # a guard that names nothing runs on every commit
  read -r -a patterns <<< "$covers"
  while IFS= read -r path; do
    [ "$path" = "scripts/guards/$2/${1##*/}" ] && return 0
    for pattern in "${patterns[@]}"; do
      # shellcheck disable=SC2053  # the right side is a pattern on purpose
      [[ $path == $pattern ]] && return 0
    done
  done <<< "$staged"
  return 1
}

ran=0; passed=0; failed=0; skipped=0; total=0; parked=0; freed=""; absent=""; empty=""
for tier in $tiers; do
  dir="$HERE/guards/$tier"
  if [ ! -d "$dir" ]; then absent="$absent $tier"; continue; fi
  printf '\n%s tier:\n' "$tier"
  found=0
  for guard in "$dir"/*.sh; do
    [ -e "$guard" ] || continue
    found=$((found + 1)); name="$(basename "$guard" .sh)"
    if ! wanted "$guard" "$tier"; then skipped=$((skipped + 1)); continue; fi
    if [ "$tier" = fast ]; then limit="$FAST_LIMIT"; else limit="$SLOW_LIMIT"; fi
    # The output goes to a file, so a program the guard leaves running cannot hold this runner waiting on it.
    cap="$(mktemp)"; start="$(now)"
    if [ -n "$TIMEOUT" ]; then "$TIMEOUT" -k 5s "${limit}s" "$BASH" "$guard" > "$cap" 2>&1; rc=$?
    else "$BASH" "$guard" > "$cap" 2>&1; rc=$?; fi
    ms=$(( $(now) - start )); total=$((total + ms)); out="$(cat "$cap")"; rm -f "$cap"
    [ "$rc" -eq 137 ] && rc=124    # stopped by force after ignoring the polite stop: the same event
    if [ "$tier" = quarantine ] && [ "$rc" -eq 0 ]; then
      freed="$freed $name"; printf '  PASSES %5dms  %s\n' "$ms" "$name"
      printf '         ^ what this guard was parked on is fixed. Move it to fast/ or nightly/.\n'
    elif [ "$tier" = quarantine ] && [ "$rc" -ne 124 ] && [[ $out == *"this mistake is present"* ]]; then
      # A parked guard prints those words over what it found. A failure without them is a broken guard,
      # which is counted as a failure below.
      parked=$((parked + 1)); printf '  known %6dms  %s\n' "$ms" "$name"
    elif [ "$rc" -eq 0 ]; then
      ran=$((ran + 1)); passed=$((passed + 1)); printf '  ok    %6dms  %s\n' "$ms" "$name"
    else
      ran=$((ran + 1)); failed=$((failed + 1))
      if [ "$rc" -eq 124 ]; then
        printf '  KILLED %5dms  %s\n         ^ stopped at the %ss limit, so it proved nothing\n' "$ms" "$name" "$limit"
      else
        printf '  FAIL  %6dms  %s\n' "$ms" "$name"
      fi
      printf '%s\n' "$out" | sed 's/^/          /'
    fi
    if [ "$tier" = fast ] && [ -n "$ALLOWED" ] && [ "$ms" -gt "$ALLOWED" ]; then
      printf '         ^ slower than the %sms a fast guard is allowed in scripts/guards/fast-budget\n' "$ALLOWED"
    fi
  done
  # An empty quarantine is the goal. Any other folder of guards holding none means its guards went missing.
  [ "$found" -eq 0 ] && [ "$tier" != quarantine ] && empty="$empty $tier"
done

printf '\n%d test(s) run, %d passed, %d failed, %dms total.\n' "$ran" "$passed" "$failed" "$total"
[ "$skipped" -gt 0 ] && printf '%d fast test(s) skipped: they cover nothing this commit stages. A run with no GUARD_SCOPE runs them all.\n' "$skipped"
if [ "$parked" -gt 0 ] || [ -n "$freed" ]; then
  printf 'quarantine: %d still failing as expected; now passing:%s\n' "$parked" "${freed:- none}"
fi
[ -n "$absent" ] && printf 'no%s tier in this repository, so nothing ran there.\n' "$absent" >&2
if [ -n "$empty" ]; then
  printf 'THE%s TIER EXISTS AND HOLDS NO GUARDS. That is a failure, not a pass.\n' "$empty" >&2
  exit 1
fi
[ "$failed" -eq 0 ] || exit 1
[ -z "$freed" ] || exit 2
exit 0
