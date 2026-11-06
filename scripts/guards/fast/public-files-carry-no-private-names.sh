#!/usr/bin/env bash
# Public files carry no private names and no third-party package
#
# WHY THIS TEST EXISTS
# This repo is public. A private list of names, kept in the git-ignored .private folder, must
# never appear in any file git would publish, and the code must run on Python alone. check.py's
# "guards" check does both and carries its own controls; this runs it on every commit, so
# nothing reaches the public repository without it.
#
# THE OTHER CHECKS THAT NEED NO BUILT WORLD RUN HERE TOO (2026-11-06). On 2026-11-04 a commit added
# scripts/guards/fast-budget while `check.py docs` refused any published file the walkthrough never
# names, and nothing ran that check at commit, so the failure landed. The worked examples, the
# manual's links and the docs take under a second together beside the names scan, so every commit
# recomputes each example against the engine and holds the README and walkthrough to the files.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
python "$REPO_ROOT/check.py" guards examples links docs
