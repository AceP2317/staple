#!/usr/bin/env bash
# A hover tip drops when a finger lifts
# COVERS: docs/*.html
#
# WHY THIS TEST EXISTS
# On 2026-11-02 the hover info on the published report vanished the moment a finger lifted, on a Pixel,
# both from a LinkedIn link and in Chrome. The layer opened on pointerover and closed on pointerout,
# which a finger fires as it lifts; it stayed only where the browser also moved focus to the tapped
# spot. The rule since that day: one tap shows the hint and keeps it, the next tap anywhere closes it.
#
# WHAT IT DOES. Runs the house tap check from ~/dev/login-starter in place on the published report,
# twice: as a phone for the hover spots, and as a landscape tablet, wide enough to show the chart, for a
# tap on the chart. Every tap runs with focus allowed and with focus blocked, and each run first plants
# a layer that drops the tip as a finger lifts and must refuse it.
#
# IT COVERS THE PUBLISHED PAGES ONLY. A code commit lands before `python run.py --pages` rebuilds docs/,
# so a change to the hover script is checked on the commit that publishes it.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
CHECK="$HOME/dev/login-starter/tools/tap-hints.mjs"
PAGE="$REPO_ROOT/docs/index.html"
if [ ! -f "$CHECK" ]; then
  printf 'FAIL the house tap check is not at %s, so nothing was checked\n' "$CHECK"
  exit 1
fi
node "$CHECK" "$PAGE" --attr data-tip --tip '#tip' || exit 1
node "$CHECK" "$PAGE" --attr data-tip --tip '#tip' --device 'iPad Pro 11 landscape' --chart 'svg[data-curve]' || exit 1
