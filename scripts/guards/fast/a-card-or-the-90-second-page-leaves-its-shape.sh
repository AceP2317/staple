#!/usr/bin/env bash
# A card or the 90-second page leaves its shape
# HOUSE-RULE: finding-first
# HOUSE-RULE: 90-second-page
# COVERS: staple/report.py staple/pages/* check.py
#
# WHY THIS TEST EXISTS
# Until 2026-11-11 the rule that a card opens with its finding ran only when someone typed
# `python check.py wording`, and it read the nine evidence cards alone. A panel beside them could run
# to any length with its takeaway at the bottom, and no commit would stop it. On the same day the pages
# gained an overview that a person can say in 90 seconds, which stays true only while the page stays short.
#
# WHAT IT DOES. Runs two of check.py's checks before a commit that touches the report code, a page
# template or the checks themselves. `wording` builds the pages fresh and reads every evidence card and
# every panel that reports or explains: its finding first, no piece over the limit, and no more words
# before its chart or table than a card may hold. `explain` reads the overview: a purpose, key points
# and functions inside their limits, no numeral typed in, every function a piece the report page names,
# and every other page's header opening the overview first.
#
# EACH CARRIES ITS OWN CONTROLS. `wording` bends a card, a panel and a step, and requires each bent copy
# to be refused. `explain` plants six faults and requires each to be refused for its own reason. A check
# that refuses nothing fails here, so a pass means the rule could have fired.
#
# IT READS THE PAGES AS BUILT FROM THIS COPY'S CODE, never the published copy, so it holds on the commit
# that changes the words, before `python run.py --pages` publishes them.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
python "$REPO_ROOT/check.py" wording explain
