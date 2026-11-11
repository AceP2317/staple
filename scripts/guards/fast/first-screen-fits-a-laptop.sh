#!/usr/bin/env bash
# The first screen fits a laptop
# COVERS: docs/index.html
#
# WHY THIS TEST EXISTS
# Until 2026-11-10 the published report opened as two panes of a fixed height. On a laptop whose browser
# leaves 1366 by 650 px for the page, the scorecard ran from 937 to 1568 px down, so a reader saw the top
# of two short panes and no result. Every check passed, because none of them opened a browser: they read
# the page's markup and its styles, and neither says where a thing lands on a screen.
#
# WHAT IT DOES. Opens the published report in a real browser, in a frame of exactly 1366 by 650 px with
# a scrollbar taking its usual room, and requires the one-sentence summary and the whole scorecard to
# lie inside that first screen. It opens the page again 360 px wide, as a phone, and requires that
# nothing runs off the side of the page or of the scorecard. On both it reads the size of every piece
# of text as drawn, a chart's labels included, against the floors `python check.py readable` holds the
# styles to. That check works from an assumed column width, and only a browser can confirm it.
#
# TWO CONTROLS. A copy of the page with three faults planted must be refused on both screens: 200 px
# of space above the scorecard, a block wider than a phone, and a line of 11 px print. The report as
# it was published before the scorecard led the page, read from this copy's history, must be refused
# on the laptop. A copy with no such history says that control did not run.
#
# IT NEEDS CHROME, found the way `python run.py --card` finds it. With no Chrome it fails and says
# nothing was checked, because a pass that opened nothing proves nothing. Give it a page as its first
# argument to try one before it is published.
#
# IT COVERS THE PUBLISHED PAGE ONLY. A code commit lands before `python run.py --pages` rebuilds docs/,
# so a change to the layout is checked on the commit that publishes it.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
PAGE="${1:-$REPO_ROOT/docs/index.html}"
[ -f "$PAGE" ] || { printf 'FAILED: %s is missing, so nothing was opened\n' "$PAGE"; exit 1; }

python - "$REPO_ROOT" "$PAGE" <<'PY'
import html
import json
import pathlib
import re
import subprocess
import sys
import tempfile

root, page_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
sys.path.insert(0, str(root))
import check    # the floors and the phone's width are check.py's own, so the two cannot drift apart
import run      # and Chrome is found the way the share card finds it

LAPTOP, PHONE = (1366, 650), (check.PHONE, 780)
LED_BY = 'class="panel score-panel"'    # the markup that first appeared when the scorecard came to lead the page
PLANT = '<div style="width:420px;height:200px"></div><p style="font-size:11px">small print</p>'
PROBE = """<script>addEventListener('load', () => {
  const box = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom].map(Math.round) }
  const card = document.querySelector('#scorecard')
  const edge = card && card.getBoundingClientRect().right
  const spill = card ? Math.round(Math.max(0, ...[...card.querySelectorAll('*')].map((e) => e.getBoundingClientRect().right - edge))) : null
  const least = { text: null, label: null }
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  while (walk.nextNode()) {
    const n = walk.currentNode, el = n.parentElement
    if (!n.nodeValue.trim() || el.closest('script, style, #tip')) continue
    const r = el.getBoundingClientRect()
    if (!r.width || !r.height) continue
    const px = parseFloat(getComputedStyle(el).fontSize) * (el.closest('svg') && el.getScreenCTM ? el.getScreenCTM().a : 1)
    const kind = el.closest('.board') ? 'label' : 'text'
    if (!least[kind] || px < least[kind][0]) least[kind] = [Math.round(px * 10) / 10, n.nodeValue.trim().slice(0, 30)]
  }
  parent.postMessage({ frame: 'NAME', wide: innerWidth, high: innerHeight, column: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth, sentence: box('.band .lede'), scorecard: box('#scorecard'), spill, least }, '*')
})</script>"""


def fail(message):
    print("FAILED: " + message)
    sys.exit(1)


def before_rebuild():
    """The report as published before the scorecard led the page, from this copy's history, or the reason it has none."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, timeout=60)
    try:
        first = git("log", "--reverse", "--format=%H", "-S" + LED_BY, "--", "docs/index.html").stdout.decode().split()
        if not first:
            return None, "no commit in this copy's history brought the scorecard to the top of the published page"
        old = git("show", first[0] + "^:docs/index.html")
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, "git could not read this copy's history (%s)" % err
    if old.returncode or LED_BY in old.stdout.decode("utf-8", "replace"):
        return None, "this copy's history does not hold the page from before that commit"
    return old.stdout.decode("utf-8", "replace"), ""


def reporting(name, text, phone):
    """A copy of a page that measures itself once loaded and tells the page holding its frame. A phone draws its
    scrollbar over the page, so the phone's copy gives the scrollbar no room."""
    at = text.rfind("</body>")
    if at < 0:
        fail("the page has no closing body tag, so it could not be measured")
    bar = "<style>html{scrollbar-width:none}</style>" if phone else ""
    return text[:at] + bar + PROBE.replace("NAME", name) + text[at:]


def laptop_faults(m):
    faults = []
    if (m["wide"], m["high"]) != LAPTOP:
        return ["the frame was %s by %s px, not %s by %s" % ((m["wide"], m["high"]) + LAPTOP)]
    for what, key in (("sentence", "sentence"), ("scorecard", "scorecard")):
        b = m[key]
        if not b:
            faults.append("no %s on the page" % what)
        elif b[0] < 0 or b[1] < 0 or b[2] > m["column"] or b[3] > m["high"]:
            faults.append("the %s runs from %s to %s px down a %s px screen" % (what, b[1], b[3], m["high"]))
    return faults + side_faults(m) + text_faults(m)


def phone_faults(m):
    if m["wide"] != PHONE[0]:
        return ["the frame was %s px wide, not %s" % (m["wide"], PHONE[0])]
    return side_faults(m) + text_faults(m)


def side_faults(m):
    faults = []
    if m["page"] > m["column"]:
        faults.append("the page is %s px wide in a %s px column, so it scrolls sideways" % (m["page"], m["column"]))
    if m["spill"]:
        faults.append("the scorecard runs %s px off its own side" % m["spill"])
    return faults


def text_faults(m):
    faults = []
    for kind, floor, what in (("text", check.TEXT_FLOOR, "text"), ("label", check.LABEL_FLOOR, "board label")):
        least = m["least"][kind]
        if least and least[0] < floor - 0.05:
            faults.append("%s drawn at %s px, under the %s px floor: '%s'" % (what, least[0], floor, least[1]))
    return faults


exe = run.chrome()
if not exe:
    fail("no Chrome was found on this machine, so nothing was checked; looked for: " + ", ".join(run.CHROMES))
page = page_path.read_text(encoding="utf-8")
planted = page.replace("<main>", "<main>" + PLANT, 1)
if planted == page:
    fail("the faults could not be planted, because the page has no <main>")
old, why_not = before_rebuild()
frames = [("page-laptop", page, LAPTOP), ("page-phone", page, PHONE), ("planted-laptop", planted, LAPTOP), ("planted-phone", planted, PHONE)]
if old:
    frames.append(("before-laptop", old, LAPTOP))

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    tmp = pathlib.Path(tmp)
    held = ['<!doctype html><meta charset="utf-8"><body style="margin:0"><script>const got = {}; addEventListener("message", '
            '(ev) => { got[ev.data.frame] = ev.data; document.body.dataset.fit = JSON.stringify(got) })</script>']
    for name, text, (wide, high) in frames:
        (tmp / (name + ".html")).write_text(reporting(name, text, wide == PHONE[0]), encoding="utf-8")
        held.append('<iframe src="%s.html" style="width:%dpx;height:%dpx;border:0;display:block"></iframe>' % (name, wide, high))
    (tmp / "frames.html").write_text("".join(held), encoding="utf-8")
    try:
        ran = subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
                              "--user-data-dir=" + str(tmp / "profile"), "--window-size=1500,3800", "--virtual-time-budget=15000",
                              "--dump-dom", (tmp / "frames.html").as_uri()], capture_output=True, timeout=100)
    except (OSError, subprocess.TimeoutExpired) as err:
        fail("the browser at %s did not finish (%s), so nothing was checked" % (exe, err))
found = re.search(r'data-fit="([^"]*)"', ran.stdout.decode("utf-8", "replace"))
got = json.loads(html.unescape(found.group(1))) if found else {}
missing = [name for name, _, _ in frames if name not in got]
if missing:
    fail("the browser measured nothing for %s, so nothing was checked" % ", ".join(missing))

refused = {name: (laptop_faults if size == LAPTOP else phone_faults)(got[name]) for name, _, size in frames}
blind = []
for name, words in (("planted-laptop", ("the scorecard runs", "under the")), ("planted-phone", ("scrolls sideways", "under the"))):
    blind += ["%s passed '%s'" % (name, w) for w in words if not any(w in f for f in refused[name])]
if old and not any("the scorecard runs" in f for f in refused["before-laptop"]):
    blind.append("the report from before the scorecard led the page passed")
if blind:
    fail("CONTROL BLIND: " + "; ".join(blind))
print("control: a copy with space planted above the scorecard, a block wider than a phone and 11 px print is refused "
      "on the laptop and on the phone")
if old:
    print("control: the report as published before the scorecard led the page is refused: "
          + next(f for f in refused["before-laptop"] if "the scorecard runs" in f))
else:
    print("control NOT RUN: the report from before the scorecard led the page, because " + why_not)

faults = ["laptop: " + f for f in refused["page-laptop"]] + ["phone: " + f for f in refused["page-phone"]]
if faults:
    fail("; ".join(faults))
lap, pho = got["page-laptop"], got["page-phone"]
print("laptop, %d by %d: the sentence ends %d px down and the scorecard %d px down, inside the first screen; the smallest "
      "text is %s px and the smallest board label %s px" % (LAPTOP + (lap["sentence"][3], lap["scorecard"][3],
                                                                      lap["least"]["text"][0], lap["least"]["label"][0])))
print("phone, %d px wide: nothing runs off the side of the page or the scorecard; the smallest text is %s px and the "
      "smallest board label %s px" % (PHONE[0], pho["least"]["text"][0], pho["least"]["label"][0]))
PY
