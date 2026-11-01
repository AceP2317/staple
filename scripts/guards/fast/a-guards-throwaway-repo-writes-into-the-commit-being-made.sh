#!/usr/bin/env bash
# A guard's throwaway repo writes into the commit being made
# COVERS: scripts/guards/* test-*.sh
#
# WHY THIS TEST EXISTS
# The fast guards run inside the commit hook, and a hook inherits the committing git's
# GIT_INDEX_FILE. On a plain `git commit` it is the relative `.git/index`, which `git -C <tmp>`
# resolves inside the throwaway repo, so nothing shows. On `git commit -- <paths>` it is an
# ABSOLUTE path to that commit's own temporary index, and `-C` does not override it. A guard that
# builds a throwaway repo then stages its planted files into the real commit.
#
# WHEN IT FIRED
# 2026-09-26 (evening), on the wrap commit. The staged-file guard's control planted notes.bak in a
# throwaway repo, the planted entry landed in the wrap commit's index, and the commit died with
# "invalid object ... for 'notes.bak'", because the file's contents lived only in the throwaway
# repo. Measured the same evening: a hook sees GIT_INDEX_FILE and no GIT_DIR.
#
# THE CLASS, NOT THE INCIDENT: any script here that runs `git init` or `git clone` must first clear
# the variables in `git rev-parse --local-env-vars`, git's own list of what points a command at one
# particular repository.

set -uo pipefail
NAME="a-guards-throwaway-repo-writes-into-the-commit-being-made"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"; export REPO_ROOT
IDIOM='unset $(git rev-parse --local-env-vars)'
# Asked once here, not per subshell: this runs on every commit, and each git start costs time.
LOCAL_VARS="$(git rev-parse --local-env-vars)"

blind() {
  printf "FAIL %s - the check is BLIND. %s\n" "$NAME" "$1"
  printf "     A clean sweep below would prove nothing while this is true.\n"
  exit 1
}

# THE CONTROL, FIRST HALF: the leak is real, and clearing the variables actually stops it. Without
# this, the sweep could insist on a line that does nothing. The stand-in for a commit's index is an
# empty one, held by a repo of its own.
# Each index write carries its own hooks pin, a folder that does not exist, so no machine-wide hook
# runs in these repos and no extra git start is paid (a-test-fixture-inherits-machine-wide-configuration-s).
( unset $LOCAL_VARS; git init -q "$TMP/real" && GIT_INDEX_FILE="$TMP/commit.index" git -C "$TMP/real" -c core.hooksPath="$TMP/real/.git/nohooks" read-tree --empty ) 2>/dev/null
[ -s "$TMP/commit.index" ] || blind "It could not build the stand-in index it compares against."
cp "$TMP/commit.index" "$TMP/before.index"
plant() {  # $1 = a dir; builds a throwaway repo there and stages a file, as a guard's control does
  git init -q "$1" 2>/dev/null
  printf 'planted\n' > "$1/planted.txt"
  git -C "$1" -c core.hooksPath="$1/.git/nohooks" add planted.txt 2>/dev/null
}
( unset $LOCAL_VARS; export GIT_INDEX_FILE="$TMP/commit.index"; plant "$TMP/leaky" )
cmp -s "$TMP/before.index" "$TMP/commit.index" \
  && blind "A throwaway repo with the commit's index inherited did not write into it, so this no longer reproduces the fault."
cp "$TMP/before.index" "$TMP/commit.index"
( unset $LOCAL_VARS; export GIT_INDEX_FILE="$TMP/commit.index"; ( unset $LOCAL_VARS; plant "$TMP/sealed" ) )
if ! cmp -s "$TMP/before.index" "$TMP/commit.index"; then
  printf "FAIL %s - the fix does not hold. With '%s' first, the throwaway\n" "$NAME" "$IDIOM"
  printf "     repo still wrote into the commit's index, so the sweep below demands a line that does nothing.\n"
  exit 1
fi

# The sweep, written once and run twice: on planted scripts, then on this repo. $1 is where to
# stand, and the rest are the folders to search from there.
# ponytail: it reads each FILE for a line asking git for that list, not each command for running
# with it cleared, so a script that asks and never clears would pass. Upgrade path: run
# each script with an absolute GIT_INDEX_FILE and compare the index before and after.
cat >"$TMP/sweep.sh" <<'__SWEEP__'
cd "$1" 2>/dev/null || { echo "BLIND: cannot enter $1"; exit 0; }
shift
files="$(find "$@" -name '*.sh' 2>/dev/null)"
[ -n "$files" ] || { echo "BLIND: no scripts found under $*"; exit 0; }
builders="$(grep -lE '^[^#]*\b(init|clone)\b' $files | xargs -r grep -lE '^[^#]*\bgit\b')"
[ -n "$builders" ] || exit 0
grep -LE '^[^#]*git rev-parse --local-env-vars' $builders \
  | sed "s|\$| builds a git repo without clearing git's repository variables first|"
__SWEEP__

# THE CONTROL, SECOND HALF: a planted script that builds a repo bare must be named, and the same
# script with the clearing line must not.
mkdir -p "$TMP/plants"
printf '#!/usr/bin/env bash\ngit -C "$T" init -q\n' > "$TMP/plants/bare.sh"
printf '#!/usr/bin/env bash\nin_t() ( %s; git -C "$T" "$@" )\nin_t init -q\n' "$IDIOM" > "$TMP/plants/cleared.sh"
control_out="$(bash "$TMP/sweep.sh" "$TMP" plants 2>/dev/null)"
case "$control_out" in
  *"bare.sh builds"*) : ;;
  *) blind "It did not name a planted script that builds a repo without clearing first. It said: ${control_out:-nothing at all}" ;;
esac
case "$control_out" in
  *"cleared.sh"*) printf "FAIL %s - it named a planted script that DOES clear first, so it names everything.\n" "$NAME"
     exit 1 ;;
esac

# THE SWEEP.
hits="$(bash "$TMP/sweep.sh" "$REPO_ROOT" scripts tools 2>/dev/null)"
if [ -n "$hits" ]; then
  printf "FAIL %s - this mistake is present:\n%s\n" "$NAME" "$hits"
  printf "     Run its git commands inside ( %s; ... ).\n" "$IDIOM"
  exit 1
fi

printf "ok   %s\n" "$NAME"
