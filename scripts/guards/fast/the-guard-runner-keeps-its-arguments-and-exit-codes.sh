#!/usr/bin/env bash
# The guard runner keeps its arguments and exit codes
# COVERS: scripts/guard.sh
#
# WHY THIS TEST EXISTS
# A commit hook starts scripts/guard.sh and acts on its exit code and on the result lines it prints.
# A change to the runner that alters either would switch that gate off without a word. check.py's
# "plain-words" check tries the runner on a scratch folder of planted guards, and refuses a copy of it
# that lets a failing guard through. This runs that check on any commit that changes the runner.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
python "$REPO_ROOT/check.py" plain-words
