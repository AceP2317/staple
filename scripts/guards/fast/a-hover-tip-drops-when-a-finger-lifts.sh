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
# WHAT IT DOES. Runs the author's tap check on every published page as a phone, for the hover spots, and
# once more on the report as a landscape tablet, wide enough to show the chart, for a tap on the chart.
# Every tap runs with focus allowed and with focus blocked, and each run first plants a layer that drops
# the tip as a finger lifts and must refuse it.
#
# THE TAP CHECK IS NOT IN THIS REPOSITORY. It is a Node script that drives a headless browser, which the
# author keeps with other tools, and a one-line file in the private folder says where it is. A copy with
# no such file fails here and says nothing was checked, because a pass that tapped nothing proves nothing.
#
# IT COVERS THE PUBLISHED PAGES ONLY. A code commit lands before `python run.py --pages` rebuilds docs/,
# so a change to the hover script is checked on the commit that publishes it.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
WHERE="$REPO_ROOT/.private/tap-check"
if [ ! -f "$WHERE" ]; then
  printf 'FAIL this copy has no .private/tap-check naming the tap check script, so nothing was checked\n'
  exit 1
fi
CHECK="$(head -n 1 "$WHERE" | tr -d '\r')"; CHECK="${CHECK/#\~/$HOME}"
if [ ! -f "$CHECK" ]; then
  printf 'FAIL the tap check is not at %s, so nothing was checked\n' "$CHECK"
  exit 1
fi
for PAGE in index manual method languages; do
  [ -f "$REPO_ROOT/docs/$PAGE.html" ] || { printf 'FAIL docs/%s.html is missing, so it was not tapped\n' "$PAGE"; exit 1; }
  node "$CHECK" "$REPO_ROOT/docs/$PAGE.html" --attr data-tip --tip '#tip' || exit 1
done
node "$CHECK" "$REPO_ROOT/docs/index.html" --attr data-tip --tip '#tip' --device 'iPad Pro 11 landscape' --chart 'svg[data-curve]' || exit 1
