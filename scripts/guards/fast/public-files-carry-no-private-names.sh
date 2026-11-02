#!/usr/bin/env bash
# Public files carry no private names and no third-party package
#
# WHY THIS TEST EXISTS
# This repo is public. A private list of names, kept in the git-ignored .private folder, must
# never appear in any file git would publish, and the code must run on Python alone. check.py's
# "guards" check does both and carries its own controls; this runs it on every commit, so
# nothing reaches the public repo without it.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
python "$REPO_ROOT/check.py" guards
