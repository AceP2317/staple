"""Three static pages: the report (index.html), its instruction manual and its methods manual.

Every figure on the report comes from a query in sql/metrics/, and every constant the manuals quote is read
from the code that uses it, so no page can state a number the code does not. The pages load nothing from the
network, carry the house build stamp, label every chart as made-up data, and give nearly every element a
hover hint with the data behind it.
"""
import datetime as dt
import hashlib
import html
import json
import math
import re
import struct
import subprocess
import zlib
from collections import Counter
from pathlib import Path

from staple import db, dispatch, load, metrics, plan, predict, triage, world

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(__file__).resolve().parent / "pages"
FILES = {"report": "index.html", "manual": "manual.html", "method": "method.html"}
PUBLISHED = ROOT / "docs"      # the copy the repository serves as its web page; `python run.py --pages` writes it
UNSTAMPED = "unstamped build"
WEIGHT_LIMIT = 2_000_000    # bytes a published page may weigh: what a phone should load for one page
PAGES_URL = "https://acep2317.github.io/staple/"   # where GitHub Pages serves docs/, read from its API when turned on
LEDE = "Plans a repair fleet's next day: who takes each call, what each truck carries, and what people do before 08:00."
CARD, CARD_VERSION, CARD_SIZE = "card.png", 1, (1200, 630)   # bump CARD_VERSION whenever the card's pixels change
STAPLE = [(7, 8, 11, 25), (7, 8, 25, 12), (21, 8, 25, 25)]   # the staple mark, as bars on a 32-unit square
# The author's house build stamp, read in place from its one copy and never re-implemented here. On a
# machine without it the page says "unstamped build" and the run prints why, rather than leaving it blank.
STAMP_MODULE = Path.home() / "dev" / "login-starter" / "lib" / "house" / "build-stamp.cjs"
SOURCES = ["staple/", "sql/", "data/", "run.py"]    # what the pages' numbers are computed from
_STAMP_JS = ("const s=require(process.argv[1]);const r=s.readBuildStamp({cwd:process.argv[2]});"
             "const d=s.fileDays({cwd:process.argv[2],under:JSON.parse(process.argv[3])});"
             "const last=d?Object.values(d).sort().pop():null;"
             "process.stdout.write(JSON.stringify({text:r.text,warnings:r.warnings,re:s.STAMP_RE.source,"
             "day:last?s.prettyDay(last):null}))")
e = html.escape

GLOSSARY = {
    "first-visit fix": "A repair finished on the technician's first visit: every part on the truck, and a repair that takes.",
    "carry list": "The parts a booked call is likeliest to need, ranked by the predictor. Its length is how many parts are on it.",
    "coverage": "The share of jobs whose every needed part was on the list or the truck. One missing part fails the whole job.",
    "held-back days": f"The last {world.TEST_DAYS} days of history. Methods learn from the days before and are scored on these.",
    "truck-day": "One technician's truck on one day: its load, its jobs and its costs.",
    "repeat visit": f"A second truck roll because the first visit did not fix the job, costed at ${load.REPEAT_VISIT:.0f} (made up).",
    "holding cost": f"The yearly cost of money tied up in truck stock: {load.HOLDING_RATE:.0%} of its value (made up).",
    "control": "A deliberately broken copy of a method with its data's links shuffled. If it scores as well, the gain is not real.",
    "lean": f"Where a model family has little history, its odds lean on the wider group, as if {predict.PULL} more jobs were seen there.",
    "regret insertion": "Placing first the call that loses most if its best technician's day fills up, not the call booked first.",
    "back-order": "A part the distribution centers cannot ship until a set day.",
    "booked code": "The problem code the booking recorded from the customer's words. Real history knows only this code.",
    "true problem": "What was really wrong. Known here only because the history is invented.",
    "unplanned call": f"A call added or moved on the day, {load.UNPLANNED:.0%} of calls (made up). No night-before list saw it.",
    "rolled call": "A call no technician's day had room for. It moves to tomorrow and counts as not fixed today.",
    "certified technician": "A technician allowed to carry and fit locked parts.",
    "locked part": "A part that may ride only on a certified technician's truck.",
    "follow-up question": f"The one question Claude asks when it is less than {triage.BLEND_BELOW:.0%} sure of the problem, "
                          "with a few answers for the customer to pick from. Made once by the model and saved.",
    "first-visit chance": "The plan's chance a call is fixed on the first visit: the chance its truck holds every part the "
                          "call needs, times the technician's success rate on that appliance.",
    "load sheet": "One truck's list for the parts room tonight: each part to put on, take off or keep, with its worth and "
                  "the booked calls behind it.",
    "worth": f"What a part is worth carrying tomorrow: each booked call's chance of needing it times a ${load.REPEAT_VISIT:.0f} "
             f"repeat visit, plus a share for unplanned calls, less its holding cost. A ${load.MOVE:.0f} move counts against "
             "it when it is not aboard, and for it when it is.",
    "current way": "The comparison on the plan: the nearest qualified technician, driving today's fixed truck stock.",
}
COLUMNS = {
    "appliance": "The appliance type.", "jobs": "Jobs counted.", "repeat_visits": "Jobs not fixed on the first visit.",
    "parts_caused": "Repeat visits where a needed part was not on the truck.",
    "parts_caused_per_100_jobs": "Parts-caused repeat visits per 100 jobs of this appliance.",
    "parts_share_of_repeats_pct": "The share of this appliance's repeat visits that a missing part caused.",
    "k": "Parts on the carry list.", "predictor_pct": "Held-back jobs whose every needed part is on the predictor's list.",
    "national_pct": "The same, for the appliance's nationally most-used parts.",
    "shuffled_pct": "The same, for the predictor trained on shuffled history (control).",
    "todays_trucks_pct": "Held-back jobs whose every needed part was on today's truck.",
    "policy": "How the truck is loaded, or how calls are handed out.",
    "parts_per_job": "Carry-list parts per booked call; 0 where the policy uses no list.",
    "covered_pct": "Jobs whose every needed part was aboard.", "parts_aboard": "Parts on the truck, per truck-day.",
    "permanent_parts": f"Parts aboard on at least {load.PERMANENT:.0%} of a truck's days.",
    "moves_a_night": "Parts put on or taken off overnight, per truck.",
    "stock_value": "Dollars of stock on board: money tied up, not spent.",
    "repeat_cost": f"Repeat visits for jobs the load missed, at ${load.REPEAT_VISIT:.0f} each, per truck-day.",
    "handling_cost": f"Parts moved overnight at ${load.MOVE:.0f} each, per truck-day.",
    "holding_cost": f"Stock value times {load.HOLDING_RATE:.0%} a year, per day.",
    "cost_a_truck_day": "Repeat, handling and holding together, per truck-day.",
    "roi": "Repeat-visit dollars a year the stock avoids, per dollar of stock on board.",
    "calls": "Held-back calls.", "rolled": "Calls rolled to tomorrow; each counts as not fixed.",
    "first_visit_fix_pct": "Expected first-visit fixes per call, a rolled call counting zero.",
    "parts_missing_pct": "Visits where a needed part was not on the truck.",
    "drive_minutes": "Drive minutes per call served.", "moved": "Calls moved to another truck by a sick day or breakdown.",
    "moved_fix_pct": "First-visit fixes among the moved calls.",
    "cost_per_call": f"Minutes at ${dispatch.MINUTE:.0f}, plus second visits at ${load.REPEAT_VISIT:.0f}, per call served.",
    "method": "How the customer's words were read.", "agrees_booked_pct": "Readings that match the booked code.",
    "agrees_true_pct": "Readings that match the true problem, known only because the data is invented.",
    "coverage_pct": "Jobs whose every needed part is on the 3-part list built from the reading.",
    "part": "The back-ordered part.", "eta_days": "Days until the centers can ship it.",
    "trucks_holding": "Trucks that hold one today.",
    "open_zones": f"Zones with no holding truck based within {load.NEARBY_MINUTES} minutes.",
    "expected_calls": "Calls forecast to need it before it ships, from training history.",
    "expected_stuck": "Of those, calls forecast to have no truck nearby holding one.",
    "dollars_at_risk": f"Expected stuck calls times ${load.REPEAT_VISIT:.0f}.",
    "actual_calls": "Calls the held-back days then brought.", "actual_stuck": "Of those, calls no truck nearby could cover.",
}
MARK_HELP = {
    "needs a certified technician": ("The part is locked and this technician is not certified.", "Send a certified technician."),
    "on this truck": ("The technician's own truck holds it.", "Nothing."),
    "from a center overnight": ("A center can load it before the visit.", "Load it tonight."),
    "on another truck nearby": (f"A truck based within {load.NEARBY_MINUTES} minutes holds it.", "Hand it over, or send that truck."),
    "back-ordered": ("No center can ship it in time and no truck nearby holds it.", "Expedite, source elsewhere, or move a spare."),
}
CHECK_ABOUT = {
    "guards": "The code imports nothing outside Python's own library and names no private name in a public file. A planted import must trip the import check.",
    "weight": f"Each published page stays under {WEIGHT_LIMIT // 1_000_000} MB, the most a phone should load for one page. A page padded past the limit must be refused.",
    "banned-control": "A name planted in a scratch file must be caught by the names scan, and a longer word containing a name must not be.",
    "predictor": "One well-populated and one thin cell are recomputed by hand from the three levels' counts. The thin cell must sit near the wider odds with the pull, and far from them without it.",
    "backtest": "Coverage on the held-back jobs. The predictor must beat the national list, and the lean must help the newest families.",
    "control": "The predictor trained on shuffled history must score no better than the national baseline.",
    "dollars": "Every load policy's costs, with planted loads recomputed by hand. No load may break the space, lock or back-order rules, and a planted bad load must show all three faults. The balanced truck must be the cheapest load, and the back-order flag must be calibrated.",
    "backorders": "The fleet back-order forecast against what the held-back days brought, within three Poisson spreads. A forecast three times too high must fail.",
    "triage": "The AI step on its cached answers, live mode against a local stand-in for the API, and its controls: shuffled answers and random replies must fall back.",
    "dispatch": "Every dispatch policy replayed. Staple must beat the nearest technician on fixes and on cost, and be cheapest on its own cost. Zero rule breaks, and a planted bad visit must show all four kinds.",
    "dispatch-control": "Shuffled skills must lose at least half the skill gain. Shuffled parts odds must stay within a point. No control may win by overbooking the day.",
    "plan": "The week's plan, coded by Claude and replayed. Every call carries Claude's code and every unsure call its question and answer; every booked call appears once; no route runs past 17:00; loads stay within space, locks and back-orders; day totals equal the replay's; each moved call names where it came from and went; and the sick calls and breakdowns equal the 92-day run's on the same days. Each rule has a planted fault that must be caught.",
    "sql": "Every metric query returns rows. The history metrics are recomputed in Python apart from the SQL, the ROI must match the replay, and planted changes in a copy must move each metric.",
    "report": "The pages carry the stamp, the results day, a made-up label and a table per chart, a hint on every element that needs one, and nothing from the network. Each page opens light with a switch to dark, and every selected mark stands out at least 3:1 from the page in both. Each page carries its tab icons and share card, the icons as drawn now and the card photographed from today's words. The published copy must match a fresh build, and planted faults must be refused.",
    "docs": "The README carries the values the code holds (job count, held-back days, seed, model, live address), neither it nor the walkthrough types a computed figure, the walkthrough names every published file, and every relative link resolves. Three planted faults must be refused.",
    "fresh-clone": "The public repository is cloned as a stranger would clone it, at this commit. The one command and every check must pass there, and the live report must equal the clone's published copy. Damaging that copy must make the clone's own report check fail.",
    "data": "Two builds from one seed must match. Planted noise sits at its rates; no part belongs to another appliance, no locked part rides uncertified, no truck runs part-empty, the newest families are thin, and furnace calls follow the seasons.",
}


SAMPLE_Q = "A blue ?\nSits beside a word the manual's glossary explains. Clicking it opens the manual in a new tab at that word."


class Raw(str):
    """Markup already escaped, passed through as it is."""


def join(*parts):
    return Raw("".join(p if isinstance(p, Raw) else e(str(p)) for p in parts))


def tipattr(text):
    return f' data-tip="{html.escape(text, quote=False).replace(chr(34), "&quot;")}" tabindex="0"'


def hv(shown, tip):
    return Raw(f'<span class="hv"{tipattr(tip)}>{e(str(shown))}</span>')


def slug(term):
    return "term-" + re.sub(r"[^a-z]+", "-", term.lower()).strip("-")


def q(term):
    return Raw(f'<a class="q" href="manual.html#{slug(term)}" target="_blank" rel="noopener"'
               f'{tipattr(term.capitalize() + chr(10) + GLOSSARY[term] + chr(10) + "The ? opens the manual at this word.")}>?</a>')


def pct(v):
    return f"{v:.1f}%"


def money(v):
    return f"${v:,.2f}" if v < 100 else f"${v:,.0f}"


def rows(results, name):
    columns, data = results[name]
    return [dict(zip(columns, r)) for r in data]


def about(name):
    """The definition a metric query opens with, read from the file itself."""
    head = [ln[2:].strip() for ln in (metrics.SQL / f"{name}.sql").read_text(encoding="utf-8").splitlines()
            if ln.startswith("--")]
    return " ".join(head)


def stamp(root=ROOT):
    """(stamp, the day the pages' sources last changed, the stamp pattern, warnings)."""
    if not STAMP_MODULE.exists():
        return UNSTAMPED, None, None, [f"the house build stamp is not on this machine, so the pages say '{UNSTAMPED}'"]
    try:
        r = subprocess.run(["node", "-e", _STAMP_JS, str(STAMP_MODULE), str(root), json.dumps(SOURCES)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        j = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as err:
        return UNSTAMPED, None, None, [f"the house build stamp could not be run: {err}"]
    warnings = list(j.get("warnings") or [])
    if r.returncode:
        warnings.append(f"node exited {r.returncode}: {(r.stderr or '').strip()[:300]}")
    return j.get("text") or UNSTAMPED, j.get("day"), j.get("re"), warnings


def tile(label, term, value, sub, tip):
    return (f'<div class="tile"{tipattr(tip)}><p class="label">{e(label)}{q(term)}</p><p class="value">{e(value)}</p>'
            f'<p class="sub">{e(sub)}</p></div>')


def bars(items):
    """Ranked horizontal bars; the row the story is about wears the accent. Items: (label, value, shown, tip, kind)."""
    top = max(v for _, v, *_ in items)
    return '<div class="bars">' + "".join(
        f'<div class="bar-row {kind}"{tipattr(tip)}><span class="bar-label">{e(label)}</span>'
        f'<span class="track"><span class="bar" style="--v:{value / top:.3f}"></span>'
        f'<span class="bar-value">{e(shown)}</span></span></div>' for label, value, shown, tip, kind in items) + "</div>"


def figure(name, title, chart, result, row_tip=None):
    columns, data = result
    head = "".join(f'<th{tipattr(c + chr(10) + COLUMNS[c])}>{e(c).replace("_", "_<wbr>")}</th>' for c in columns)
    body = "".join("<tr>" + "".join(
        f'<td{tipattr(row_tip(dict(zip(columns, r))))}><span class="hv">{e(str(v))}</span></td>' if i == 0 and row_tip
        else f"<td>{e(str(v))}</td>" for i, v in enumerate(r)) + "</tr>" for r in data)
    madeup = (f"Invented history\n{world.JOBS:,} jobs from seed {world.SEED}. No real company, customer, part or "
              "technician. The method is real; this figure is not.")
    return (f'<figure><figcaption><span class="fig-title"{tipattr(title + chr(10) + about(name) + chr(10) + f"{len(data)} rows from sql/metrics/{name}.sql.")}>'
            f'{e(title)}</span> <span class="madeup"{tipattr(madeup)}>made-up data</span></figcaption>{chart}'
            f'<details{"" if chart else " open"}><summary{tipattr(f"Show the numbers{chr(10)}The {len(data)} rows this chart is drawn from, exactly as sql/metrics/{name}.sql returns them.")}>'
            f'Show the numbers</summary><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
            f'</table></div></details></figure>')


def curve(points, jobs):
    """Coverage against carry-list length: three lines, the axis carrying the values, a crosshair for hover."""
    w, h, left, right, top, bottom = 560, 300, 56, 18, 16, 48
    ks = [p["k"] for p in points]
    x = [left + i * (w - left - right) / (len(ks) - 1) for i in range(len(ks))]

    def y(v):
        return top + (100 - v) * (h - top - bottom) / 100

    series = [("predictor_pct", "Predictor", "--s1", "Ranks parts by how often they fixed the same model family and problem, leaning wider where history is thin."),
              ("national_pct", "National top parts", "--s2", "The appliance's nationally most-used parts, whatever the problem: today's stocking logic."),
              ("shuffled_pct", "Shuffled history (control)", "--dim", "The predictor trained on history whose part-to-problem links were shuffled. It must fall to the national line.")]
    today = points[0]["todays_trucks_pct"]
    svg = [f'<line class="grid" x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
           f'<text class="tick" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v}%</text>' for v in (0, 25, 50, 75, 100)]
    svg += [f'<text class="tick" x="{xi:.1f}" y="{h - bottom + 20}" text-anchor="middle">{k}</text>' for xi, k in zip(x, ks)]
    svg.append(f'<text class="tick" x="{(left + w - right) / 2:.1f}" y="{h - 6}" text-anchor="middle">parts on the carry list</text>')
    svg.append(f'<line class="ref" x1="{left}" x2="{w - right}" y1="{y(today):.1f}" y2="{y(today):.1f}"/>'
               f'<text class="ref-label" x="{left + 8}" y="{y(today) - 7:.1f}">today\'s trucks: {pct(today)}</text>')
    for key, _, color, _ in series:
        line = " ".join(f"{xi:.1f},{y(p[key]):.1f}" for xi, p in zip(x, points))
        svg.append(f'<polyline class="series" style="stroke:var({color})" points="{line}"/>'
                   + "".join(f'<circle class="dot" style="fill:var({color})" cx="{xi:.1f}" cy="{y(p[key]):.1f}" r="3.5"/>'
                             for xi, p in zip(x, points)))
    lead = points[-1]["predictor_pct"]
    svg.append(f'<text class="end-label" x="{x[-1] - 10:.1f}" y="{y(lead) - 12:.1f}" text-anchor="end">'
               f'predictor, {ks[-1]} parts: {pct(lead)}</text>')
    svg.append(f'<line class="cross" x1="0" x2="0" y1="{top}" y2="{h - bottom}"/>'
               f'<rect class="hit" x="{left}" y="{top}" width="{w - left - right}" height="{h - top - bottom}"/>')
    tips = [f"{p['k']} part{'s' if p['k'] > 1 else ''} on the list: predictor covers {pct(p['predictor_pct'])}\n"
            f"National top parts {pct(p['national_pct'])}, so the predictor adds {p['predictor_pct'] - p['national_pct']:+.1f} points.\n"
            f"Shuffled history (control) {pct(p['shuffled_pct'])}; today's trucks {pct(today)}.\n"
            f"Share of the {jobs:,} held-back jobs whose every needed part is on the list." for p in points]
    data = {"w": w, "xs": x, "ks": ks, "tips": tips}
    keys = "".join(f'<span class="key"{tipattr(n + chr(10) + t)}><i style="background:var({c})"></i>{e(n)}</span>'
                   for _, n, c, t in series)
    return (f'<div class="legend">{keys}</div><svg viewBox="0 0 {w} {h}" tabindex="0" data-curve="{e(json.dumps(data))}" '
            f'aria-label="First-visit coverage against carry-list length; the table below holds every value">{"".join(svg)}</svg>')


def stamp_line(stamp_text, day):
    return (f'<p class="stamp"><span class="build-stamp"{tipattr(stamp_text + chr(10) + "The build that made this page: how many changes this repository has had, the day it was built (Eastern time) and the change it was built from. A + means it was built from changes not yet saved.")}>'
            f'{e(stamp_text)}</span> · results last changed <span class="content-day"{tipattr((day or "unknown") + chr(10) + "The last day any code, query or cached answer that feeds these pages changed. A rebuild that moved nothing keeps it.")}>'
            f'{e(day or "unknown")}</span></p>')


NAV = {"report": ("Instruction Manual", "manual.html", "Opens the instruction manual in a new tab: what each figure means, every label, every threshold, and what Staple cannot tell you."),
       "method": ("Methods and logic", "method.html", "Opens the methods manual in a new tab: every formula, every rule of the replays, the prompts, the queries and the checks."),
       "back": ("Back to the report", "index.html", "Returns to the report.")}


def shell(kind, title, lede, body, stamp_text, day, chosen=None):
    buttons = {"report": ["report", "method"], "manual": ["back", "method"], "method": ["back", "report"]}[kind]
    nav = "".join(f'<a class="btn{"" if i == 0 else " ghost"}" href="{NAV[b][1]}"{"" if b == "back" else " target=\"_blank\" rel=\"noopener\""}'
                  f'{tipattr(NAV[b][0] + chr(10) + NAV[b][2])}>{e(NAV[b][0])}</a>' for i, b in enumerate(buttons))
    nav += (f'<button type="button" class="btn ghost" data-theme-toggle'
            f'{tipattr("Light or dark" + chr(10) + "The page opens light. This switches it, and this browser remembers the choice.")}>Dark mode</button>')
    css, js = (PAGES / "house.css").read_text(encoding="utf-8"), (PAGES / "tips.js").read_text(encoding="utf-8")
    if kind == "report":
        js += "\n" + (PAGES / "plan.js").read_text(encoding="utf-8")
    app = f' class="app" data-side="plan" data-chosen="{chosen}"' if kind == "report" else ""
    h1_tip = ("Staple\nPlans a repair fleet's next day on invented history: Claude codes the bookings, Staple routes the "
              "technicians and loads the trucks, and people act on a to-do list before 08:00.")
    remembered = 'try{if(localStorage.getItem("staple-theme")==="dark")document.documentElement.dataset.theme="dark"}catch(e){console.warn("Staple: the saved light/dark choice could not be read",e)}'
    url = PAGES_URL + ("" if kind == "report" else FILES[kind])
    share = "".join(f'<meta property="og:{k}" content="{e(str(v))}">' for k, v in (
        ("type", "website"), ("site_name", "Staple"), ("title", title), ("description", lede), ("url", url),
        ("image", f"{PAGES_URL}{CARD}?v={CARD_VERSION}"), ("image:width", CARD_SIZE[0]), ("image:height", CARD_SIZE[1]),
        ("image:alt", "Staple: " + LEDE)))
    head = (f'<meta name="description" content="{e(lede)}">{share}<meta name="twitter:card" content="summary_large_image">'
            '<link rel="icon" href="icon.svg" type="image/svg+xml"><link rel="icon" href="icon-32.png" sizes="32x32">'
            '<link rel="apple-touch-icon" href="apple-touch-icon.png">')
    return (f'<!doctype html>\n<html lang="en" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(title)}</title>{head}<style>{css}</style><script>{remembered}</script></head><body{app}><header class="band"><div class="band-in"><div>'
            f'<h1{tipattr(h1_tip)}>{e("Staple" if kind == "report" else title)}</h1><p class="lede">{e(lede)}</p>{stamp_line(stamp_text, day)}</div>'
            f'<nav class="actions">{nav}</nav></div></header><main>{body}'
            f'<footer>{e(written_in("these pages"))} Nothing is installed beyond Python. Every number comes from the queries in '
            f'{hv("sql/metrics/", "sql/metrics/" + chr(10) + ", ".join(p.stem for p in sorted(metrics.SQL.glob("*.sql"))))}. '
            f'Designed and directed by Ian Provencher, built with Claude Code.</footer></main><div id="tip" hidden></div>'
            f'<script>{js}</script></body></html>\n')


def languages():
    """How many files of each language the repository holds, counted on each build so no page states a stale count."""
    return {"Python": len(list(ROOT.glob("*.py")) + list((ROOT / "staple").glob("*.py"))),
            "SQL": len(list((ROOT / "sql").glob("*.sql")) + list(metrics.SQL.glob("*.sql"))),
            "metric queries": len(list(metrics.SQL.glob("*.sql"))), "JavaScript": len(list(PAGES.glob("*.js")))}


def written_in(where="the pages"):
    n = languages()
    return (f"Written in Python, SQL and JavaScript: {n['Python']} Python files invent the history, predict parts, load "
            f"trucks, ask Claude, replay dispatch, write {where} and check it all; {n['SQL']} SQL files hold the tables "
            f"and the {n['metric queries']} metric queries; {n['JavaScript']} JavaScript files run the hints and switches.")


def tokens():
    """The light scheme's colors, read from the stylesheet so the icons and the card never hold a second copy."""
    light = re.search(r":root\{([^}]*)\}", (PAGES / "house.css").read_text(encoding="utf-8")).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", light))


def mark_svg(size=32):
    t = tokens()
    bars = "".join(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"/>' for x0, y0, x1, y1 in STAPLE)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="{size}" height="{size}">'
            f'<rect width="32" height="32" rx="7" fill="{t["--band"]}"/><g fill="{t["--cta"]}">{bars}</g></svg>')


def mark_png(size):
    """The mark as an opaque square PNG, four by four samples a pixel. Opaque, because iOS puts a clear icon on black."""
    t = tokens()
    band, cta = (bytes.fromhex(t[k][1:]) for k in ("--band", "--cta"))
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            hit = sum(any(x0 <= (x + (i + .5) / 4) * 32 / size < x1 and y0 <= (y + (j + .5) / 4) * 32 / size < y1
                          for x0, y0, x1, y1 in STAPLE) for i in range(4) for j in range(4)) / 16
            row += bytes(round(b + (c - b) * hit) for b, c in zip(band, cta))
        rows.append(bytes(row))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def assets():
    """The files published beside the pages: the tab icons, drawn here, and the share card, photographed once."""
    return {"icon.svg": mark_svg().encode(), "icon-32.png": mark_png(32), "apple-touch-icon.png": mark_png(180),
            CARD: (PAGES / CARD).read_bytes()}


def card_html():
    """The share card as a 1200 by 630 page. `python run.py --card` photographs it into staple/pages/card.png."""
    w, h = CARD_SIZE
    root = re.search(r":root\{[^}]*\}", (PAGES / "house.css").read_text(encoding="utf-8")).group(0)
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{root}'
            f'html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden}}*{{box-sizing:border-box}}'
            f'body{{background:var(--band);color:var(--band-ink);font-family:system-ui,"Segoe UI",sans-serif;padding:70px 80px;'
            f'display:flex;flex-direction:column;justify-content:space-between}}.top{{display:flex;align-items:center;gap:30px}}'
            f'h1{{font-size:110px;line-height:1;margin:0;letter-spacing:-2px}}p{{margin:0}}'
            f'.lede{{font-size:42px;line-height:1.28;max-width:1040px;text-wrap:balance}}.steps{{font-size:26px;font-weight:700;color:var(--cta);white-space:nowrap}}'
            f'.foot{{font-size:25px;color:var(--band-2);margin-top:10px}}</style></head><body>'
            f'<div class="top">{mark_svg(116)}<h1>Staple</h1></div><p class="lede">{e(LEDE)}</p>'
            f'<div><p class="steps">Claude codes every booking · Staple plans the trucks · people act before 08:00</p>'
            f'<p class="foot">Every name and figure is invented; the method is real · {PAGES_URL.split("//")[1].rstrip("/")}</p>'
            f'</div></body></html>')


def card_fingerprint():
    return hashlib.sha256(card_html().encode("utf-8")).hexdigest()


def day_name(day):
    return dt.date.fromisoformat(day).strftime("%a %d %b").replace(" 0", " ")


def chance_class(c):
    return "c5" if c >= 0.9 else "c4" if c >= 0.8 else "c3" if c >= 0.7 else "c2" if c >= 0.5 else "c1"


def ai_line(c):
    a = c["ai"]
    if a["question"]:
        settled = (f"the customer's answer coded it {c['problem']}" if a["reply"]
                   else f"no answer fit, so it runs on {c['problem']}")
        return f"Claude, {a['first_confidence']:.0%} sure, asked one question; {settled}."
    return f"Claude read {c['problem']}, {a['confidence']:.0%} sure."


def call_tip(c, ctx):
    rate = ctx.crew.skill[(c["tech"], c["type"])][0]
    why = (f"Why {c['tech']}: lowest expected cost; next best {c['runner']}, ${c['gap']:.0f} more."
           if c["runner"] else f"Why {c['tech']}: the only one with room.")
    return "\n".join([
        f"Call {c['job']} · {plan.clock(c['start'])}–{plan.clock(c['end'])} · zone {c['zone'] + 1}"
        + (" · urgent" if c["priority"] == 1 else ""),
        f"{c['type'].capitalize()}: “{c['words']}”", ai_line(c),
        "Parts: " + "; ".join(f"{p['name']} {p['odds']:.0%} {p['where']}" for p in c["parts"]) + ".",
        f"First-visit chance {c['chance']:.0%} ({c['tech']} fixes {rate:.0%} of {c['type']}s).", why])


def ran_tip(c, moved):
    lines = [f"Call {c['job']} as the day ran · {plan.clock(c['start'])}–{plan.clock(c['end'])} · {c['tech']}",
             f"{c['type'].capitalize()}: {c['problem']}. First-visit chance {c['chance']:.0%}.",
             "Every likely part was aboard." if c["covered"] else "A needed part was not aboard: a second visit."]
    if c["job"] in moved:
        m = moved[c["job"]]
        why = "called out sick" if m["why"] == "sick" else "broke down"
        lines.append(f"Moved from {m['from']}, who {why}. First-visit chance {m['before']:.0%} on the planned truck, "
                     f"{m['after']:.0%} on this one." if m["before"] is not None else f"Moved from {m['from']}, who {why}.")
    if c["unplanned"]:
        lines.append("Added on the day, so no night plan saw it.")
    if c["changed"]:
        lines.append("Escalated to urgent on the day." if c["changed"] == 1 else "Eased to low priority on the day.")
    return "\n".join(lines)


def at(minutes):
    return f"{100 * minutes / dispatch.DAY:.1f}%"


def board(d, ctx, view):
    m = d["morning"]
    moved = {x["job"]: x for x in m["moved"]}
    ticks = "".join(f'<span style="left:{at(60 * h)}">{8 + h:02d}</span>' for h in range(dispatch.DAY // 60 + 1))
    rows = []
    for tech in ctx.crew.techs:
        info = ctx.techs[tech]
        tip = (f"{tech} · truck {info['truck']}\n{info['level'].capitalize()} {info['profile']}, zone {info['home'] + 1}"
               + (", certified." if info["certified"] else "."))
        blocks, kind = [], ""
        if view == "night":
            for c in d["trucks"][tech]["calls"]:
                blocks.append(f'<span class="blk {chance_class(c["chance"])}{" ai" if c["ai"]["question"] else ""}" '
                              f'style="left:{at(c["start"])};width:{at(c["end"] - c["start"])}"{tipattr(call_tip(c, ctx))}></span>')
        else:
            for c in d["trucks"][tech]["ran"]:
                marks = "".join(f" {k}" for k, on in (("moved", c["job"] in moved), ("unpl", c["unplanned"]),
                                                      ("miss", not c["covered"]), ("up", c["changed"] == 1)) if on)
                blocks.append(f'<span class="blk {chance_class(c["chance"])}{marks}" style="left:{at(c["start"])};'
                              f'width:{at(c["end"] - c["start"])}"{tipattr(ran_tip(c, moved))}>'
                              f'{"from " + moved[c["job"]]["from"] if c["job"] in moved else ""}</span>')
            gave = sum(x["from"] == tech for x in m["moved"])
            if tech in m["sick"]:
                kind = " sick"
                blocks.append(f'<span class="lane-note"{tipattr(f"{tech} called out sick at 07:00" + chr(10) + f"The night plan and the loads were already made. {gave} calls planned for {tech} moved to other trucks, which could use only the parts they already carried.")}>'
                              f'called out sick at 07:00 · {gave} calls moved</span>')
            if tech in m["broken"]:
                kind, when = " broken", m["broken"][tech]
                blocks.append(f'<span class="dead" style="left:{at(when)}"></span><span class="brk" style="left:{at(when)}"'
                              f'{tipattr(f"Truck {info["truck"]} broke down at {plan.clock(when)}" + chr(10) + f"Its {m["stranded"][tech]} parts are stranded for the day. {gave} later calls moved to other trucks.")}>'
                              f'&#9888; broke down {plan.clock(when)} · {gave} moved</span>')
        rows.append(f'<div class="row{kind}" data-tech="{tech}"><span class="who"{tipattr(tip)}>{tech}</span>'
                    f'<span class="lane">{"".join(blocks)}</span></div>')
    return (f'<div class="board v-{view}"><div class="row axis"><span></span><span class="lane ticks">{ticks}</span></div>'
            f'{"".join(rows)}</div>')


def spot(zone, salt, ring=1.0):
    """A stop's place inside its zone's square: spread by the call number, since routes run zone to zone."""
    x, y = plan.ZONES[zone]
    angle, radius = (salt * 2.399963) % 6.2832, (8 + (salt % 4) * 7) * ring
    return 50 + 100 * x + radius * math.cos(angle), 50 + 100 * y + radius * math.sin(angle)


def route_map(d, ctx, centers):
    m = d["morning"]
    moved = {x["job"]: x for x in m["moved"]}
    cells = "".join(f'<rect class="zone" x="{100 * x + 3}" y="{100 * y + 3}" width="94" height="94" rx="8"/>'
                    f'<text class="zone-n" x="{100 * x + 9}" y="{100 * y + 16}">zone {i + 1}</text>'
                    for i, (x, y) in enumerate(plan.ZONES))
    marks = "".join(f'<rect class="center" x="{100 * plan.ZONES[z][0] + 44}" y="{100 * plan.ZONES[z][1] + 44}" width="12" height="12"/>'
                    for _, z in centers)
    homes = {t: spot(ctx.techs[t]["home"], n * 7 + 3, 1.2) for n, t in enumerate(ctx.crew.techs)}
    layers = {"night": [], "ran": []}
    for tech in ctx.crew.techs:
        hx, hy = homes[tech]
        for view in layers:
            calls = d["trucks"][tech]["calls" if view == "night" else "ran"]
            pts = [spot(c["zone"], c["job"]) for c in calls]
            line = " ".join(f"{x:.0f},{y:.0f}" for x, y in [homes[tech]] + pts)
            stops = []
            for i, ((x, y), c) in enumerate(zip(pts, calls), 1):
                if view == "night":
                    stops.append(f'<circle class="stop{" ai" if c["ai"]["question"] else ""}" cx="{x:.0f}" cy="{y:.0f}" r="4.5"'
                                 f'{tipattr(f"Stop {i} · {tech} · {plan.clock(c["start"])}" + chr(10) + f"Call {c["job"]}, {c["type"]}, {c["chance"]:.0%}.")}/>')
                elif c["job"] in moved:
                    stops.append(f'<line class="hand" x1="{hx:.0f}" y1="{hy:.0f}" x2="{x:.0f}" y2="{y:.0f}"/>'
                                 f'<circle class="stop moved" cx="{x:.0f}" cy="{y:.0f}" r="4.5"'
                                 f'{tipattr(f"Call {c["job"]} moved from {moved[c["job"]]["from"]} to {tech}" + chr(10) + f"Reached at {plan.clock(c["start"])}, with only the parts on {tech}" + chr(39) + "s truck.")}/>')
            if view == "ran" and tech in m["broken"]:
                x, y = pts[-1] if pts else homes[tech]
                stops.append(f'<text class="brk-map" x="{x + 5:.0f}" y="{y - 4:.0f}">&#9888;</text>')
            layers[view].append(f'<g class="rt" data-tech="{tech}"><polyline points="{line}"/>{"".join(stops)}'
                                f'<circle class="home" cx="{hx:.0f}" cy="{hy:.0f}" r="2.5"/></g>')
    tip = ("Routes, zone to zone\nTwelve zones 15 km apart; the squares are the two distribution centers. Every truck's "
           "route is drawn faint and the chosen one bold, with its stops numbered. A stop sits inside its zone; drive "
           "times run zone to zone, not on roads.")
    return (f'<svg class="map" viewBox="0 0 400 300"{tipattr(tip)} aria-label="Map of the twelve zones and every truck route">'
            f'{cells}{marks}<g class="v-night">{"".join(layers["night"])}</g><g class="v-ran">{"".join(layers["ran"])}</g></svg>')


def sheet_html(d, tech, ctx, shown):
    s, info, calls = d["trucks"][tech]["sheet"], ctx.techs[tech], d["trucks"][tech]["calls"]
    rows = s["rows"]
    moves = [r for r in rows if r["status"] != "keep"]
    keep = [r for r in rows if r["status"] == "keep"]

    def why(r):
        if r["status"] == "take off":
            return "no call needs it" if r["worth"] <= 0 else "outranked for space"
        reasons = [f"call {j} {p:.0%}" for j, p in r["calls"]]
        if r["spare"] >= 0.5:
            reasons.append(f"spare for unplanned calls {money(r['spare'])}")
        return ", ".join(reasons) or "spare for unplanned calls"
    head = "".join(f"<th{tipattr(t + chr(10) + h)}>{e(t)}</th>" for t, h in (
        ("Do", "Tonight, at the center."), ("Part", "With its made-up cost."), ("Worth", "Dollars, tomorrow."),
        ("Why", "The calls behind it.")))
    body = "".join(f'<tr class="{r["status"].replace(" ", "-")}"><td>{e(r["status"])}</td><td>{e(r["name"])} ({money(r["cost"])})</td>'
                   f'<td>{money(r["worth"])}</td><td>{e(why(r))}</td></tr>' for r in moves)
    needed = [r["name"] for r in keep if r["calls"]]
    spare = Counter(ctx.kind[r["part"]] for r in keep if not r["calls"])
    aboard = (f"{len(keep)} parts. For tomorrow's calls: {e(', '.join(needed)) or 'none'}. Stock for unplanned calls: "
              + (", ".join(f"{e(k)} {n}" for k, n in sorted(spare.items())) or "none"))
    title = f"Truck {info['truck']} · {tech}, {info['level']} {info['profile']}" + (", certified" if info["certified"] else "")
    table = f'<table class="load"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>' if moves else "<p>No change to the load tonight.</p>"
    return (f'<div class="sheet" data-sheet="{tech}"{"" if shown else " hidden"}><h4>{e(title)}</h4>'
            f'<p class="sub">{s["space"]} of {s["capacity"]} space units · {money(s["value"])} of stock aboard · put on '
            f'{sum(r["status"] == "put on" for r in rows)}, take off {sum(r["status"] == "take off" for r in rows)}, keep {len(keep)}</p>'
            f'{table}<p class="keep"><strong>Keep aboard:</strong> {aboard}.</p><p class="sub">{len(calls)} calls tomorrow; '
            f'the board and the map show the route.</p></div>')


TODO_PROOF = {"Parts room": "inventory_roi", "Call center": "ai_step", "Dispatch": "dispatch_scorecard", "Buyer": "backorder_exposure"}


def ai_panel(d, ctx):
    a = d["ai"]
    cards = []
    for c in a["calls"]:
        ai = c["ai"]
        first = ctx.labels[f"{c['type']}.{ai['first']}"]
        pick = ' class="pick"'
        opts = "".join(f'<li{pick if o["code"] == ai["reply"] else ""}>{e(o["text"])}</li>' for o in ai["offered"])
        then = f"Coded {c['problem']}" if ai["reply"] else "Still unsure: the call center rings the customer"
        where = f"{c['tech']} at {plan.clock(c['start'])}" if c.get("tech") else "no technician had room"
        now = ", ".join(p["name"] for p in c["parts"])
        before = ", ".join(c["first_parts"])
        tip = (f"Call {c['job']}: Claude at booking\nThe customer said \"{c['words']}\". Claude's first reading was {first} at "
               f"{ai['first_confidence']:.0%}, below the {triage.BLEND_BELOW:.0%} it needs, so it asked one question. "
               + (f"The answer coded the call {c['problem']}." if ai["reply"]
                  else f"No offered answer fit, so the call runs on {c['problem']} and goes to the call center."))
        parts = (f'<p class="parts"><span>First guess would load</span> {e(before)}<br><span>Now loads</span> {e(now)}</p>'
                 if now and before and now != before else "")
        cards.append(f'<div class="ask"{" data-tech=" + chr(34) + c["tech"] + chr(34) if c.get("tech") else ""}{tipattr(tip)}>'
                     f'<p class="said">{e(c["type"].capitalize())}: &ldquo;{e(c["words"])}&rdquo;</p><p>Claude read <strong>{e(first)}</strong> '
                     f'<span class="conf"><i style="width:{100 * ai["first_confidence"]:.0f}%"></i></span> {ai["first_confidence"]:.0%}</p>'
                     f'<p class="asked">It asked: &ldquo;{e(ai["question"])}&rdquo;</p>'
                     f'<ul class="opts">{opts}</ul><p class="then">{e(then)} · {e(where)}</p>{parts}</div>')
    tip = (f"AI at booking\nClaude Haiku 5.5 ({triage.MODEL}) read every booking's own words and picked the problem code "
           f"this plan runs on. Below {triage.BLEND_BELOW:.0%} sure, it asked the customer one question. Its answers were "
           "made once by the model and saved with the code, so this page calls no model and holds no key.")
    return (f'<section class="panel ai-panel"><h3{tipattr(tip)}>AI at booking: Claude coded all {a["coded"]} bookings{q("follow-up question")}</h3>'
            f'<p>{a["sure"]} read {triage.BLEND_BELOW:.0%} sure or more were coded at once. {a["asked"]} were unsure, so Claude '
            f'asked each customer one question: {a["settled"]} answers settled the code, and {a["asked"] - a["settled"]} go to '
            f'the call center. Each unsure call is below, and wears a yellow corner on the board.</p>'
            f'<div class="asks">{"".join(cards)}</div></section>')


def todo_panel(d):
    groups = []
    for owner in TODO_PROOF:
        items = [i for i in d["todo"] if i["owner"] == owner]
        if not items:
            continue
        tag = '<b class="ai-tag">AI</b>' if owner == "Call center" else ""
        lis = "".join(f'<li class="todo-item"{" data-tech=" + chr(34) + i["tech"] + chr(34) if i["tech"] else ""} data-proof="{TODO_PROOF[owner]}"'
                      f'{tipattr(i["text"] + chr(10) + i["detail"])}>{tag}{e(i["text"])}</li>' for i in items)
        groups.append(f'<div class="todo-group"><h4>{e(owner)} · {e(items[0]["when"])} · {len(items)}</h4><ul>{lis}</ul></div>')
    tip = ("To do before 08:00\nWhat people act on, by owner. Click an item to light up its truck, route and load sheet, "
           "and to open the evidence behind that kind of action on the right.")
    return f'<section class="panel"><h3{tipattr(tip)}>To do before 08:00</h3><div class="todo">{"".join(groups)}</div></section>'


def morning_panel(d, ctx):
    m = d["morning"]
    items = []

    def moves(tech, chance):
        return "".join(f'<li>call {x["job"]} &rarr; {x["to"] or "rolled to tomorrow"}'
                       + (f', first-visit chance {x["before"]:.0%} &rarr; {x["after"]:.0%}' if chance and x["to"] and x["before"] is not None else "")
                       + "</li>" for x in m["moved"] if x["from"] == tech)
    for tech in m["sick"]:
        n = sum(x["from"] == tech for x in m["moved"])
        items.append(f'<li class="evt" data-tech="{tech}" data-view="ran"{tipattr(f"{tech} called out sick at 07:00" + chr(10) + "The night plan and the loads were already made, so each call moved by the same rule to a truck that could take it, using only the parts that truck carried. Click to see it on the board.")}>'
                     f'<strong>{tech} called out sick at 07:00</strong>: {n} calls moved<ul>{moves(tech, True)}</ul></li>')
    for tech, when in m["broken"].items():
        n = sum(x["from"] == tech for x in m["moved"])
        items.append(f'<li class="evt" data-tech="{tech}" data-view="ran"{tipattr(f"Truck {ctx.techs[tech]["truck"]} broke down at {plan.clock(when)}" + chr(10) + f"{m["stranded"][tech]} parts stranded with it. The calls it had not reached moved to other trucks. Click to see it on the board.")}>'
                     f'<strong>Truck {ctx.techs[tech]["truck"]} ({tech}) broke down at {plan.clock(when)}</strong>: '
                     f'{m["stranded"][tech]} parts stranded, {n} calls moved<ul>{moves(tech, True)}</ul></li>')
    if not m["sick"] and not m["broken"]:
        items.append("<li>No sick calls and no breakdowns on this day.</li>")
    up = sum(p == 1 for _, p in m["changed"].values())
    items.append(f'<li>{len(m["changed"])} calls changed priority on the day, {up} of them escalated to urgent (&#9650; on the board).</li>')
    tip = ("07:00 and after\nWhat changed after the plan was made: sick calls at 07:00, breakdowns during the day and "
           "priority changes. Switch the board to As the day ran to see each call move.")
    return f'<section class="panel"><h3{tipattr(tip)}>07:00 and after: what changed</h3><ul class="morning">{"".join(items)}</ul></section>'


def summary_tiles(d):
    o, b = d["ours"], d["base"]
    saved = (b["served"] - b["fixes"]) - (o["served"] - o["fixes"])
    return "".join([
        tile("Booked calls", "rolled call", f"{int(o['calls'])}", f"on {len(d['trucks'])} trucks; {int(o['rolled'])} rolled",
             f"{int(o['calls'])} calls booked for {day_name(d['day'])}\nAll invented. {int(o['rolled'])} could not fit any route "
             f"and roll to tomorrow, against {int(b['rolled'])} for the current way."),
        tile("Fixed on the first visit", "first-visit fix", f"{100 * o['fixes'] / o['calls']:.1f}%",
             f"against {100 * b['fixes'] / b['calls']:.1f}% the current way",
             f"{o['fixes']:.0f} of {int(o['calls'])} calls expected fixed first time\nThe current way, the nearest technician on "
             f"today's fixed stock: {b['fixes']:.0f}. Same calls, same sick calls and breakdowns, both on Claude's codes."),
        tile("Second visits avoided", "repeat visit", f"{saved:.0f}", f"at a made-up {money(load.REPEAT_VISIT)} each",
             f"{saved:.1f} fewer second visits than the current way\nCost a call {money(o['cost'] / o['served'])} against "
             f"{money(b['cost'] / b['served'])}."),
        tile("Parts moved tonight", "load sheet", f"{d['moves']}", "put on or taken off at the centers",
             f"{d['moves']} parts moved overnight across the fleet\nA truck keeps a part only while it is worth more than its "
             "space, its holding cost and a move.")])


def day_section(d, ctx, centers, shown, chosen):
    views = (("night", "Plan at night", "Routes and loads as planned the night before, from the booked calls."),
             ("ran", "As the day ran", "After the 07:00 sick calls, the breakdowns, the calls added on the day and the priority changes."))
    pick = "".join(f'<button class="vbtn{" on" if v == "night" else ""}" data-view="{v}"{tipattr(t + chr(10) + h)}>{t}</button>' for v, t, h in views)
    legend = ('<p class="legend-line"' + tipattr("Reading the board\nEach block is a call, from its start to its end. Its shade is "
              "the plan's first-visit chance. Marks show what Claude asked, what moved, what was added and what went wrong.")
              + '>First-visit chance: <span class="sw c1"></span>under 50% <span class="sw c2"></span>50% <span class="sw c3"></span>70% '
              '<span class="sw c4"></span>80% <span class="sw c5"></span>90%+ · <span class="sw ai-sw"></span>Claude asked · '
              '<span class="sw moved-sw"></span>moved · <span class="sw unpl-sw"></span>added on the day · '
              '<span class="sw miss-sw"></span>part missing · &#9650; escalated</p>')
    sheets = "".join(sheet_html(d, tech, ctx, tech == chosen) for tech in ctx.crew.techs)
    heading = "The plan for " + day_name(d["day"])
    return (f'<section class="day" data-day="{d["day"]}"{"" if shown else " hidden"}>'
            f'<h2{tipattr(heading + chr(10) + "An invented day, coded by Claude and planned call by call.")}>{e(heading)} '
            f'<span class="invented"{tipattr("Invented day" + chr(10) + "Every call, technician, part and figure on this day is invented. The method is real.")}>invented</span></h2>'
            f'<div class="tiles small">{summary_tiles(d)}</div>{ai_panel(d, ctx)}{todo_panel(d)}{morning_panel(d, ctx)}'
            f'<section class="panel"><h3{tipattr("Routes and the board" + chr(10) + "Click a technician, a block or a route to light up that truck everywhere.")}>Routes and the board</h3>'
            f'<div class="switch">{pick}</div><div class="mapboard">{route_map(d, ctx, centers)}<div>{board(d, ctx, "night")}'
            f'{board(d, ctx, "ran")}</div></div>{legend}</section>'
            f'<section class="panel"><h3{tipattr("Load sheet" + chr(10) + "The chosen truck: what the parts room loads tonight, and the route it runs tomorrow.")}>'
            f'Load sheet{q("load sheet")}</h3>{sheets}</section></section>')


def day_proof(d, shown):
    o, b = d["ours"], d["base"]

    def diff(x, y, fmt):
        return ("+" if x >= y else "−") + (f"{100 * abs(x - y):.1f} points" if fmt is share else fmt(abs(x - y)))

    def share(v):
        return f"{100 * v:.1f}%"
    rows = [("Fixed on the first visit", o["fixes"] / o["calls"], b["fixes"] / b["calls"], share),
            ("Second visits", o["served"] - o["fixes"], b["served"] - b["fixes"], lambda v: f"{v:.0f}"),
            ("Drive minutes a call", o["drive"] / o["served"], b["drive"] / b["served"], lambda v: f"{v:.1f}"),
            ("Cost a call", o["cost"] / o["served"], b["cost"] / b["served"], money),
            ("Calls rolled to tomorrow", o["rolled"], b["rolled"], lambda v: f"{v:.0f}")]
    head = "".join(f"<th{tipattr(t + chr(10) + h)}>{e(t)}</th>" for t, h in (
        ("Measure", "What is compared."), ("Staple", "This plan: Claude's codes, the skills-and-parts dispatch rule and balanced loads."),
        ("Current way", "The nearest qualified technician on today's fixed truck stock, with the same calls and surprises."),
        ("Difference", "Staple against the current way.")))
    body = "".join(f"<tr><td>{e(n)}</td><td>{f(x)}</td><td>{f(y)}</td><td>{diff(x, y, f)}</td></tr>" for n, x, y, f in rows)
    saved = (b["served"] - b["fixes"]) - (o["served"] - o["fixes"])
    tip = (f"{day_name(d['day'])}, against the current way\nBoth replays of this day run on Claude's codes, with the same sick "
           "calls, breakdowns and calls added on the day.")
    return (f'<div class="card day-proof" data-day="{d["day"]}"{"" if shown else " hidden"}><h3{tipattr(tip)}>'
            f'{e(day_name(d["day"]))}, against the current way</h3><p class="sub">Both on Claude\'s codes, with the same surprises.</p>'
            f'<table class="cmp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'<p class="do"><strong>What it means:</strong> on this invented day the plan avoids {saved:.0f} second visits. '
            f'That is the method\'s gain on invented data, not a result: the same comparison on real history is the first '
            f'thing to run.</p></div>')


def tech_card(tech, ctx, shown):
    info = ctx.techs[tech]
    skills = sorted((t, r, s) for (x, t), (r, s) in ctx.crew.skill.items() if x == tech)
    head = "".join(f"<th{tipattr(t + chr(10) + h)}>{e(t)}</th>" for t, h in (
        ("Appliance", "An appliance this technician can repair."), ("Fixes first time", "Their success rate with the parts aboard."),
        ("Speed", "Their repair time against an average technician's; below 1 is faster.")))
    body = "".join(f"<tr><td>{e(t)}</td><td>{r:.0%}</td><td>{s:.2f}</td></tr>" for t, r, s in skills)
    tip = f"{tech}\nThe chosen technician: what the dispatch rule knows about them. Choose another on the board or the map."
    return (f'<div class="card tech-card" data-tech-card="{tech}"{"" if shown else " hidden"}><h3{tipattr(tip)}>'
            f'{tech} · {e(info["level"])} {e(info["profile"])}{", certified" if info["certified"] else ""}, truck {info["truck"]}</h3>'
            f'<table class="cmp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'<p class="do"><strong>Why it matters:</strong> each call goes to the technician with the lowest expected cost, so '
            f'a specialist fast and sure on one appliance draws those calls even from farther away.</p></div>')


def build_report(results, week, stamp_text, day):
    names = [n for n, *_ in dispatch.POLICIES]
    disp = {r["policy"]: r for r in rows(results, "dispatch_scorecard")}
    now, loaded, skills, parts_only, ours = (disp[n] for n in names)
    shuffled_skills = disp[dispatch.SHUFFLED_SKILLS]
    calls = ours["calls"]
    best = {}
    for r in rows(results, "inventory_roi"):
        if r["policy"] not in best or r["cost_a_truck_day"] < best[r["policy"]]["cost_a_truck_day"]:
            best[r["policy"]] = r
    fixed, bal = best[load.TODAY], best[load.BALANCED]
    ai = {r["method"]: r for r in rows(results, "ai_step")}
    claude, keywords = ai["Claude"], ai["keyword rules"]
    shuffled_ai = next(r for m, r in ai.items() if m.startswith("Claude answers shuffled"))
    asked = ai["Claude, asking when unsure"]
    random_replies = next(r for m, r in ai.items() if m.startswith("Claude, asking when unsure, random"))
    booked = ai["booked code"]
    causes = rows(results, "parts_caused_reschedules")
    fill = {r["appliance"]: r for r in rows(results, "truck_fill_rate")}
    repeats, caused, all_jobs = (sum(r[c] for r in causes) for c in ("repeat_visits", "parts_caused", "jobs"))
    worst = causes[0]
    points = rows(results, "carry_list_curve")
    three = next(p for p in points if p["k"] == load.SHOWN_K)
    stuck = rows(results, "backorder_exposure")
    R = load.REPEAT_VISIT
    ctx, open_day = week["ctx"], week["open"]
    od = next(d for d in week["days"] if d["day"] == open_day)
    receivers = Counter(x["to"] for x in od["morning"]["moved"] if x["to"])
    chosen = receivers.most_common(1)[0][0] if receivers else next(i["tech"] for i in od["todo"] if i["tech"])

    tiles = "".join([
        tile("Fixed on the first visit", "first-visit fix", pct(ours["first_visit_fix_pct"]),
             f"from {pct(now['first_visit_fix_pct'])} sending the nearest technician on today's stock",
             f"{pct(ours['first_visit_fix_pct'])} of {calls:,} held-back calls fixed on the first visit\n"
             f"Nearest technician, today's stock {pct(now['first_visit_fix_pct'])}; nearest, balanced loads "
             f"{pct(loaded['first_visit_fix_pct'])}; skills only {pct(skills['first_visit_fix_pct'])}; parts only "
             f"{pct(parts_only['first_visit_fix_pct'])}.\nA rolled call counts as not fixed; {ours['rolled']} were rolled.\n"
             "From dispatch_scorecard.sql."),
        tile("Cost a call", "repeat visit", f"${ours['cost_per_call']:.2f}",
             f"from ${now['cost_per_call']:.2f}: minutes at ${dispatch.MINUTE:.0f}, second visits at ${R:.0f}, both made up",
             f"${ours['cost_per_call']:.2f} a call, against ${now['cost_per_call']:.2f} today\n"
             f"Drive {ours['drive_minutes']} minutes a call against {now['drive_minutes']}. Second visits at ${R:.0f} for "
             f"the {100 - ours['first_visit_fix_pct']:.1f}% not fixed first time, against {100 - now['first_visit_fix_pct']:.1f}%.\n"
             f"Minutes at ${dispatch.MINUTE:.0f} each. Both rates are made up.\nFrom dispatch_scorecard.sql."),
        tile("Jobs the truck covers", "coverage", pct(bal["covered_pct"]),
             f"from {pct(fixed['covered_pct'])}, at ${bal['cost_a_truck_day']:.0f} a truck-day against ${fixed['cost_a_truck_day']:.0f}",
             f"{pct(bal['covered_pct'])} of jobs had every needed part aboard the balanced truck\n"
             f"Today's trucks {pct(fixed['covered_pct'])} at ${fixed['cost_a_truck_day']:.2f} a truck-day; balanced "
             f"${bal['cost_a_truck_day']:.2f}.\nBalanced: {bal['parts_aboard']:.0f} parts aboard, {bal['permanent_parts']:.0f} "
             f"of them permanent, {bal['moves_a_night']} moved a night, ${bal['stock_value']:,.0f} of stock.\nFrom inventory_roi.sql."),
        tile("Customer's words read right", "booked code", pct(claude["agrees_booked_pct"]),
             f"agreement with the booked code, against {pct(keywords['agrees_booked_pct'])} for keyword rules",
             f"{pct(claude['agrees_booked_pct'])} of {claude['jobs']:,} held-back bookings: Claude's code matches the booked code\n"
             f"Keyword rules {pct(keywords['agrees_booked_pct'])}; Claude's answers shuffled (control) {pct(shuffled_ai['agrees_booked_pct'])}.\n"
             f"Against the true problem: Claude {pct(claude['agrees_true_pct'])}, keyword rules {pct(keywords['agrees_true_pct'])}.\n"
             "From ai_step.sql.")])

    cause_bars = bars([(r["appliance"], r["parts_caused_per_100_jobs"], f"{r['parts_caused_per_100_jobs']:.1f} per 100 jobs",
                        f"{r['parts_caused_per_100_jobs']:.1f} repeat visits per 100 {r['appliance']} jobs for a missing part\n"
                        f"{r['parts_caused']:,} of {r['jobs']:,} jobs; {r['parts_share_of_repeats_pct']:.1f}% of its "
                        f"{r['repeat_visits']:,} repeat visits.\nTruck fill: {pct(fill[r['appliance']]['line_fill_pct'])} of needed "
                        f"part lines aboard, {pct(fill[r['appliance']]['job_fill_pct'])} of jobs with every line aboard "
                        "(truck_fill_rate.sql).", "lead" if r is worst else "") for r in causes])
    load_bars = bars([(r["policy"] + (f", {r['parts_per_job']} per job" if r["policy"] in (load.TOP_UP, load.LISTS_ONLY) else ""),
                       r["cost_a_truck_day"], f"${r['cost_a_truck_day']:.0f} · covers {pct(r['covered_pct'])}",
                       f"${r['cost_a_truck_day']:.2f} a truck-day, covering {pct(r['covered_pct'])} of jobs\n"
                       f"Repeat visits ${r['repeat_cost']:.2f} + parts moved ${r['handling_cost']:.2f} + holding ${r['holding_cost']:.2f}.\n"
                       f"{r['parts_aboard']:.0f} parts aboard ({r['permanent_parts']:.0f} permanent), {r['moves_a_night']} moved a night, "
                       f"${r['stock_value']:,.0f} of stock; ROI {r['roi']:.0f} dollars a year per dollar held.",
                       "lead" if r is bal else "") for r in sorted(best.values(), key=lambda r: r["cost_a_truck_day"])])
    dispatch_bars = bars([(r["policy"], r["first_visit_fix_pct"], f"{pct(r['first_visit_fix_pct'])} · ${r['cost_per_call']:.0f} a call",
                           f"{pct(r['first_visit_fix_pct'])} fixed on the first visit, ${r['cost_per_call']:.2f} a call\n"
                           f"Drive {r['drive_minutes']} minutes a call; a part missing on {pct(r['parts_missing_pct'])} of visits; "
                           f"{r['rolled']} of {r['calls']:,} calls rolled.\n{r['moved']} calls moved by sick days and breakdowns, "
                           f"fixed first time {pct(r['moved_fix_pct'])}.\nAgainst the nearest technician on today's stock: "
                           f"{r['first_visit_fix_pct'] - now['first_visit_fix_pct']:+.1f} points, "
                           f"{r['cost_per_call'] - now['cost_per_call']:+.2f} dollars a call.",
                           "lead" if r is ours else "control" if "control" in r["policy"] else "") for r in disp.values()])
    ai_bars = bars([(r["method"], r["coverage_pct"], f"list covers {pct(r['coverage_pct'])} · agrees {pct(r['agrees_booked_pct'])}",
                     f"The 3-part list built from this reading covers {pct(r['coverage_pct'])} of {r['jobs']:,} jobs\n"
                     f"{pct(r['agrees_booked_pct'])} of bookings match the booked code; {pct(r['agrees_true_pct'])} match the true "
                     "problem, known only because the data is invented.",
                     "lead" if r is asked else "control" if "control" in r["method"] else "")
                    for r in (claude, asked, booked, keywords, shuffled_ai)])

    def stuck_tip(r):
        return (f"{r['part']}: ships in {r['eta_days']} days\n{r['trucks_holding']} trucks hold one; {r['open_zones']} zones have "
                f"no holder within {load.NEARBY_MINUTES} minutes.\nForecast {r['expected_calls']} calls, {r['expected_stuck']} with no "
                f"truck nearby: ${r['dollars_at_risk']:,.0f} at risk.\nThe held-back days brought {r['actual_calls']} calls, "
                f"{r['actual_stuck']} stuck.")

    exp_calls, exp_stuck = sum(r["expected_calls"] for r in stuck), sum(r["expected_stuck"] for r in stuck)
    act_calls, act_stuck = sum(r["actual_calls"] for r in stuck), sum(r["actual_stuck"] for r in stuck)

    def card(name, heading, text, fig, do):
        tip = f"{heading}\nSource: sql/metrics/{name}.sql. {about(name)}"
        return (f'<section class="card" id="proof-{name}"><h3{tipattr(tip)}>{e(heading)}</h3><p>{text}</p>{fig}'
                f'<p class="do"><strong>What a fleet does with this:</strong> {do}</p></section>')

    proof = [
        card("dispatch_scorecard", "Who takes the call", join(
            "Every held-back day replayed with the same made-up sick calls, breakdowns and priority changes. Staple sends "
            "each call to the technician with the lowest expected cost: drive and repair minutes, plus a second visit times the "
            "chance that technician's skill or truck fails it, placing first the call with most to lose", q("regret insertion"),
            ". Shuffling the technicians' skills drops it to ",
            hv(pct(shuffled_skills["first_visit_fix_pct"]), f"{pct(shuffled_skills['first_visit_fix_pct'])} with shuffled skills\n"
               f"Each technician read another's success rate and speed; ${shuffled_skills['cost_per_call']:.2f} a call."),
            ", below the parts-only rule's ",
            hv(pct(parts_only["first_visit_fix_pct"]), f"{pct(parts_only['first_visit_fix_pct'])} on parts alone\nEvery technician "
               f"taken as average; ${parts_only['cost_per_call']:.2f} a call."),
            ", so the gain comes from knowing who is good at what. Calls moved by a sick day or a breakdown were fixed first time ",
            hv(pct(ours["moved_fix_pct"]), f"{pct(ours['moved_fix_pct'])} of {ours['moved']} moved calls fixed first time\n"
               f"The nearest technician on today's stock, facing the same surprises: {pct(now['moved_fix_pct'])} of {now['moved']}."),
            f" of the time, against {pct(now['moved_fix_pct'])} for the current way. A rolled call", q("rolled call"),
            " counts as not fixed."),
            figure("dispatch_scorecard", "Calls fixed on the first visit, with a rolled call counted as not fixed", dispatch_bars,
                   results["dispatch_scorecard"]),
            "keep the routing system dispatch already owns, and hand it this expected-cost score for each technician and call, "
            "so its routes carry the skill and parts judgment."),
        card("inventory_roi", "What to load tonight", join(
            "Each night the balanced truck ranks every part by its worth", q("worth"), " per unit of space. It covers ",
            hv(pct(bal["covered_pct"]), f"{pct(bal['covered_pct'])} of jobs covered\nAgainst {pct(fixed['covered_pct'])} on today's trucks."),
            " of jobs for ", hv(f"${bal['cost_a_truck_day']:.0f} a truck-day", f"${bal['cost_a_truck_day']:.2f} a truck-day\nRepeat "
                               f"${bal['repeat_cost']:.2f}, moves ${bal['handling_cost']:.2f}, holding ${bal['holding_cost']:.2f}."),
            q("truck-day"), f", against {pct(fixed['covered_pct'])} and ",
            hv(f"${fixed['cost_a_truck_day']:.0f}", f"${fixed['cost_a_truck_day']:.2f} a truck-day on today's stock\nRepeat "
               f"${fixed['repeat_cost']:.2f}, moves ${fixed['handling_cost']:.2f}, holding ${fixed['holding_cost']:.2f}."),
            " for today's fixed stock, while holding ",
            hv(f"${bal['stock_value']:,.0f}", f"${bal['stock_value']:,.0f} of stock on the balanced truck\n{bal['parts_aboard']:.0f} parts, "
               f"{bal['permanent_parts']:.0f} of them aboard nearly every day."),
            f" against ${fixed['stock_value']:,.0f}."),
            figure("inventory_roi", "Cost a truck-day: repeat visits, parts moved overnight, and money tied up", load_bars,
                   results["inventory_roi"]),
            "switch the overnight load from a fixed stock list to tomorrow's booked calls, so the parts room works from a load "
            "sheet per truck like the one on the left."),
        card("ai_step", "Claude coding the bookings", join(
            f"Claude Haiku 5.5 ({triage.MODEL}) reads the customer's own words and picks a problem code with a confidence. "
            "Asking one question when it is unsure, its 3-part list covers ",
            hv(pct(asked["coverage_pct"]), f"{pct(asked['coverage_pct'])} of jobs\nAgreeing with the booked code on {pct(asked['agrees_booked_pct'])}."),
            " of jobs, against ", hv(pct(booked["coverage_pct"]), f"{pct(booked['coverage_pct'])} with the booked code\nThe code a "
                                     "call-taker picked, taken as given."), " for the booked code", q("booked code"), " and ",
            hv(pct(keywords["coverage_pct"]), f"{pct(keywords['coverage_pct'])} for keyword rules\nThe code whose label shares the most "
               "words with the customer's."), " for keyword rules. So Claude codes a booking about as well as a call-taker, and "
            "far better than keyword rules. Random replies to its question drop it to ",
            hv(pct(random_replies["coverage_pct"]), f"{pct(random_replies['coverage_pct'])} with random replies\nThe control: the "
               "question only helps when the answer is real."),
            ", and shuffling its answers between bookings to ",
            hv(pct(shuffled_ai["coverage_pct"]), f"{pct(shuffled_ai['coverage_pct'])} with shuffled answers\nThe control: the reading "
               "only helps when it belongs to the words."),
            ". The answers are saved with the code, so these pages rebuild without a key."),
            figure("ai_step", "Jobs whose every needed part is on the 3-part list, by how the words were read", ai_bars, results["ai_step"]),
            "let Claude code the bookings and ask its one question at the moment of booking, and keep a person on the few calls "
            "its question cannot settle."),
        card("backorder_exposure", "Back-orders to chase first", join(
            "For each back-ordered part", q("back-order"), ", the list forecasts the calls that will need it before it arrives, "
            "from training history only, and how many have no truck nearby holding one. Over the held-back months it expected ",
            hv(f"{exp_calls:.0f} calls and {exp_stuck:.0f} stuck", f"{exp_calls:.1f} calls, {exp_stuck:.1f} stuck\n"
               f"${sum(r['dollars_at_risk'] for r in stuck):,.0f} at risk across {len(stuck)} parts."),
            "; the days then brought ", hv(f"{act_calls} and {act_stuck}", f"{act_calls} calls, {act_stuck} stuck\nCounted on the "
                                         "held-back days, which the forecast never saw."), "."),
            figure("backorder_exposure", "Back-ordered parts, by repeat-visit dollars at risk before arrival", "",
                   results["backorder_exposure"], row_tip=stuck_tip),
            "expedite from the top of this list, and move spares toward the zones where no truck holds one."),
        card("carry_list_curve", "Which parts to carry", join(
            "The predictor ranks parts by how often they fixed the same model family and problem, and leans", q("lean"),
            " on the wider appliance group when a family's history is thin. Three parts on the carry list", q("carry list"),
            " cover ", hv(pct(three["predictor_pct"]), f"{pct(three['predictor_pct'])} of held-back jobs\nEvery needed part on a "
                          f"{load.SHOWN_K}-part list. With {points[-1]['k']} parts: {pct(points[-1]['predictor_pct'])}."),
            " of held-back jobs; the national top three cover ",
            hv(pct(three["national_pct"]), f"{pct(three['national_pct'])} with the national top {load.SHOWN_K}\nThe appliance's "
               "most-used parts, whatever the problem."), ". Shuffling the history drops the predictor to ",
            hv(pct(three["shuffled_pct"]), f"{pct(three['shuffled_pct'])} on shuffled history\nThe control falls to the national "
               "line, so the gain comes from what the history links."), "."),
            figure("carry_list_curve", "Held-back jobs whose every needed part is on the list", curve(points, calls),
                   results["carry_list_curve"]),
            "rank each booked call's parts by model family and problem rather than by national usage, and feed those lists to "
            "the overnight load."),
        card("parts_caused_reschedules", "Where repeat visits come from", join(
            "On today's trucks, a missing part causes ",
            hv(f"{100 * caused / repeats:.0f}%", f"{caused:,} of {repeats:,} repeat visits\nAcross {all_jobs:,} jobs. The rest failed "
               "on the technician's success rate with every part aboard."), " of repeat visits", q("repeat visit"), ". ",
            f"{worst['appliance'].capitalize()}s are worst: ",
            hv(f"{worst['parts_caused_per_100_jobs']:.0f} of every 100", f"{worst['parts_caused']:,} of {worst['jobs']:,} "
               f"{worst['appliance']} jobs\nOnly {pct(fill[worst['appliance']]['line_fill_pct'])} of the part lines they need "
               "are on the truck."), f" {worst['appliance']} jobs need a second visit because a part was not on board."),
            figure("parts_caused_reschedules", "Repeat visits caused by a missing part, per 100 jobs", cause_bars,
                   results["parts_caused_reschedules"]),
            f"start the pilot on {worst['appliance']}s, where a missing part costs the most second visits."),
    ]
    adopt = ('<section class="card"><h3' + tipattr("How a fleet would adopt it\nThe order I would run it in on real history, "
             "each step with the result that decides the next.") + '>How a fleet would adopt it</h3><ol class="steps">'
             '<li><strong>Back-test on its own history.</strong> The same replay on real calls, parts and technicians, before '
             'any truck changes. If the gains vanish there, it stops here.</li>'
             '<li><strong>Pilot one region.</strong> Load and dispatch one region by this plan for six weeks against a matched '
             'region left as it is, and compare first-visit fix, parts-caused repeat visits and stock value.</li>'
             '<li><strong>Roll out region by region.</strong> Each region goes live only after the one before it beat its match.</li></ol></section>')
    limits = ('<section class="card"><h3' + tipattr("What this shows, and what it does not\nThe instruction manual's last sections "
              "list every limit; the methods manual names each shortcut and its upgrade.") + '>What this shows, and what it does not</h3>'
              f'<p>The plan on the left runs on Claude\'s codes for one invented week. The evidence above runs on the call-taker\'s '
              f'booked codes for all {world.TEST_DAYS} held-back days, so it measures the planning apart from the reading. Every '
              'result reproduces from one seed, and each gain has a shuffled control that falls back when the history\'s links '
              'are broken. None of it shows the gains on a real fleet, because the history is invented. Three corners are cut on '
              'purpose: counting instead of a trained model, zone-to-zone drive times instead of road routing, and a call-by-call '
              'night plan instead of a solver.</p></section>')

    dbtn = []
    for d in week["days"]:
        m = d["morning"]
        sick, broken = len(m["sick"]), len(m["broken"])
        tip = (f"{day_name(d['day'])}\n{int(d['ours']['calls'])} calls; "
               + (f"{sick} sick call{'s' * (sick > 1)}" if sick else "no sick calls") + ", "
               + (f"{broken} breakdown{'s' * (broken > 1)}" if broken else "no breakdowns") + ".")
        dbtn.append(f'<button class="day-btn{" on" if d["day"] == open_day else ""}" data-day="{d["day"]}"{tipattr(tip)}>'
                    f'{e(day_name(d["day"]))}{"<i class=ev-s>&#10010;</i>" * sick}{"<i class=ev-b>&#9888;</i>" * broken}</button>')
    steps = "".join(f'<li{tipattr(t + chr(10) + h)}><strong>{e(t)}</strong>{e(s)}</li>' for t, s, h in (
        ("1. Claude codes every booking.", "It reads the customer's own words and picks the problem, asking one question when unsure.",
         "Claude Haiku 5.5, on answers made once by the model and saved with the code. The AI at booking panel shows each unsure call."),
        ("2. Staple plans the trucks.", "Which technician takes each call, in what order, and what each truck loads tonight.",
         "The dispatch rule and the balanced truck, replayed call by call. The board, the map and the load sheet show it."),
        ("3. People act before 08:00.", "The parts room loads, the call center and dispatch ring customers, the buyer chases back-orders.",
         "The to-do list names each action, its owner and the truck or call it concerns.")))
    madeup = (f"Invented history\n{world.JOBS:,} jobs from seed {world.SEED}, with wrong descriptions, thin model histories and "
              "patternless failures planted on purpose. No real company, customer, part or technician.")
    week_tip = (f"Day\nThe most recent {plan.WEEK} held-back days, not hand-picked. The page opens on the most recent day with "
                "a sick call and a breakdown that both moved calls. &#10010; marks a sick call, &#9888; a breakdown.")
    sides = "".join(f'<button class="side-btn{" on" if s == "plan" else ""}" data-side="{s}"{tipattr(t + chr(10) + h)}>{t}</button>'
                    for s, t, h in (("plan", "Plan", "What to do tonight."), ("proof", "Proof", "Why the plan beats the current way.")))
    intro = (f'<div class="intro"><ol class="how">{steps}</ol><div class="pickers"><span class="pick-label"{tipattr(html.unescape(week_tip))}>'
             f'Day</span>{"".join(dbtn)}<span class="invented"{tipattr(madeup)}>every name and figure is invented; the method is real</span>'
             f'<span class="sides">{sides}</span></div></div>')
    left = "".join(day_section(d, ctx, week["centers"], d["day"] == open_day, chosen) for d in week["days"])
    across = (f'<section class="card"><h3{tipattr(f"Across all {world.TEST_DAYS} held-back days" + chr(10) + "The scorecard runs on the call-taker" + chr(39) + "s booked codes, so it measures the planning apart from the reading.")}>'
              f'Across all {world.TEST_DAYS} held-back days, on the booked codes</h3><div class="tiles">{tiles}</div></section>')
    right = ("".join(day_proof(d, d["day"] == open_day) for d in week["days"])
             + "".join(tech_card(t, ctx, t == chosen) for t in ctx.crew.techs) + across + "".join(proof) + adopt + limits)
    body = (f'{intro}<div class="panes"><div class="pane plan-pane"><p class="pane-label">The plan · one invented week, '
            f'on Claude\'s codes</p>{left}</div><div class="pane proof-pane"><p class="pane-label">The proof · why it beats '
            f'the current way</p>{right}</div></div>')
    return shell("report", "Staple report", LEDE, body, stamp_text, day, chosen)


def as_sent(template):
    """A prompt template as the model receives it: doubled braces single, the {fields} still named."""
    return template.replace("{{", "{").replace("}}", "}")


def facts():
    """Every value the manuals quote, read from the code that uses it."""
    cat, start = world.CATALOGUE, world.START
    n_parts, n_problems = [len(cat[t][1]) for t in cat], [len(cat[t][2]) for t in cat]
    v = {"jobs": f"{world.JOBS:,}", "days": f"{world.DAYS:,}", "start": f"{start.day} {start:%b %Y}", "types": len(cat),
         "techs": world.TECHS, "test_days": world.TEST_DAYS, "seed": world.SEED, "families": world.FAMILIES_PER_TYPE,
         "rare_families": world.RARE_FAMILIES, "misdescribed": f"{world.MISDESCRIBED:.0%}", "random_failure": f"{world.RANDOM_FAILURE:.0%}",
         "vague": f"{world.VAGUE_WORDS:.0%}", "capacity": world.CAPACITY, "stock_fill": f"{world.STOCK_FILL:.0%}",
         "zones": len(world.ZONES), "parts_min": min(n_parts), "parts_max": max(n_parts), "problems_min": min(n_problems),
         "problems_max": max(n_problems), "pull": predict.PULL, "shown_k": load.SHOWN_K, "ks": f"{min(load.KS)} to {max(load.KS)}",
         "repeat_visit": f"${load.REPEAT_VISIT:.0f}", "move": f"${load.MOVE:.0f}", "holding_rate": f"{load.HOLDING_RATE:.0%}",
         "unplanned": f"{load.UNPLANNED:.0%}", "nearby": load.NEARBY_MINUTES, "lean_tolerance": f"${load.LEAN_TOLERANCE:.0f}",
         "permanent": f"{load.PERMANENT:.0%}", "backorder_flag": f"{load.BACKORDER_FLAG:.0%}", "model": triage.MODEL,
         "effort": triage.EFFORT, "api_version": triage.API_VERSION, "blend_below": triage.BLEND_BELOW, "day": dispatch.DAY,
         "minute": f"${dispatch.MINUTE:.0f}", "sick": f"{dispatch.SICK:.0%}", "breakdown": f"{dispatch.BREAKDOWN:.0%}",
         "escalate": f"{dispatch.ESCALATE:.0%}", "ease": f"{dispatch.EASE:.0%}",
         "REPAIR": ", ".join(f"{t} {m}" for t, m in dispatch.REPAIR.items()),
         "URGENT": ", ".join(sorted(dispatch.URGENT)), "LOW": ", ".join(sorted(dispatch.LOW)),
         "SYSTEM": triage.SYSTEM, "TEMPLATE": as_sent(triage.TEMPLATE), "FOLLOWUP_SYSTEM": triage.FOLLOWUP_SYSTEM,
         "FOLLOWUP_TEMPLATE": as_sent(triage.FOLLOWUP_TEMPLATE), "week": plan.WEEK, "likely": f"{plan.LIKELY:.0%}",
         "behind": f"{plan.BEHIND:.0%}"}
    return {k: Raw(e(str(x))) for k, x in v.items()}


def thresholds():
    made, design = "made up: replace with a real figure", "design choice"
    items = [("Held-back days", f"{world.TEST_DAYS} days", "The back-test's hold-out.", design),
             ("Pull toward wider groups", f"{predict.PULL} jobs", "How far thin history leans on the wider level.", "design choice; check.py backtest measures its effect"),
             ("Carry list quoted", f"{load.SHOWN_K} parts", "The list length the report quotes.", design),
             ("Repeat visit", f"${load.REPEAT_VISIT:.0f}", "A second truck roll.", made),
             ("Moving a part", f"${load.MOVE:.0f}", "Loading or unloading one part overnight.", made),
             ("Holding rate", f"{load.HOLDING_RATE:.0%} a year", "Cost of money tied up in stock.", made),
             ("Unplanned share", f"{load.UNPLANNED:.0%}", "Calls added or moved on the day.", made),
             ("Nearby", f"{load.NEARBY_MINUTES} minutes", "Another truck counts as nearby within this drive.", design),
             ("List-length tolerance", f"${load.LEAN_TOLERANCE:.0f} a truck-day", "The shortest list within this of the cheapest is chosen.", design),
             ("Permanent", f"{load.PERMANENT:.0%} of days", "A part aboard this often counts as permanent.", design),
             ("Back-order flag", f"{load.BACKORDER_FLAG:.0%} odds", "A booking at this chance of needing a stuck part is flagged.", made),
             ("Truck space", f"{world.CAPACITY} units", "Space on one truck.", made),
             ("Today's fill", f"{world.STOCK_FILL:.0%}", "How full today's trucks are kept.", made),
             ("AI confidence", f"{triage.BLEND_BELOW}", "Below it, a reading leans on the appliance's overall odds and a follow-up question is asked.", design),
             ("Working day", f"{dispatch.DAY} minutes", "08:00 to 17:00.", design),
             ("Minute", f"${dispatch.MINUTE:.0f}", "One minute of a technician and truck.", made),
             ("Sick day", f"{dispatch.SICK:.0%} a day", "Chance a technician calls out.", made),
             ("Breakdown", f"{dispatch.BREAKDOWN:.0%} a day", "Chance a truck breaks down.", made),
             ("Priority change", f"{dispatch.ESCALATE:.0%} up, {dispatch.EASE:.0%} down", "Chance a call escalates or eases on the day.", made)]
    items += [(f"Repair time, {t}", f"{m} minutes", "An average technician's time on this appliance.", made) for t, m in dispatch.REPAIR.items()]
    body = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td><td>{e(d)}</td></tr>" for a, b, c, d in items)
    return Raw(f'<div class="scroll"><table class="wrap"><thead><tr><th>Threshold</th><th>Value</th><th>What it does</th>'
               f'<th>What sets it</th></tr></thead><tbody>{body}</tbody></table></div>')


def ponytails():
    """Each deliberate shortcut, read from the marker comment where the code cuts it."""
    found = []
    for path in sorted((ROOT / "staple").glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "# ponytail:" in line:
                text = [line.split("# ponytail:", 1)[1].strip()]
                for nxt in lines[i + 1:]:
                    if not nxt.strip().startswith("#"):
                        break
                    text.append(nxt.strip().lstrip("#").strip())
                found.append(f"<li><code>{e(path.name)}</code>: {e(' '.join(text))}</li>")
    return Raw("<ul>" + "".join(found) + "</ul>")


def checks():
    names = re.findall(r'@check\("([^"]+)"\)', (ROOT / "check.py").read_text(encoding="utf-8"))
    return Raw("<dl>" + "".join(f"<dt><code>{e(n)}</code></dt><dd>{e(CHECK_ABOUT[n])}</dd>" for n in names) + "</dl>")


def sql_metrics():
    out = []
    for path in sorted(metrics.SQL.glob("*.sql")):
        out.append(f'<h3 id="sql-{e(path.stem)}">{e(path.stem)}</h3><p>{e(about(path.stem))}</p>'
                   f'<pre>{e(path.read_text(encoding="utf-8").strip())}</pre>')
    return Raw("".join(out))


def fill_in(template, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], (PAGES / template).read_text(encoding="utf-8"))


def build_manuals(stamp_text, day):
    v = facts()
    v["sample_q"] = Raw(tipattr(SAMPLE_Q))
    v["MARKS"] = Raw("".join(f"<tr><td>{e(m)}</td><td>{e(MARK_HELP[m][0])}</td><td>{e(MARK_HELP[m][1])}</td></tr>" for m in load.MARKS))
    v["MARK_ORDER"] = Raw("".join(f"<li><strong>{e(m)}</strong>: {e(MARK_HELP[m][0])}</li>" for m in load.MARKS))
    v["THRESHOLDS"] = thresholds()
    v["GLOSSARY"] = Raw("<dl>" + "".join(f'<dt id="{slug(t)}"><strong>{e(t)}</strong></dt><dd>{e(d)}</dd>'
                                         for t, d in sorted(GLOSSARY.items())) + "</dl>")
    v["SQL_METRICS"], v["CHECKS"], v["PONYTAILS"] = sql_metrics(), checks(), ponytails()
    manual = shell("manual", "Staple instruction manual", "What each figure means, how to read it, and what it cannot tell you.",
                   fill_in("manual.html", v), stamp_text, day)
    method = shell("method", "Staple methods manual", "Every formula, rule, prompt, query and check behind the report.",
                   fill_in("method.html", v), stamp_text, day)
    return manual, method


def build(results, week, stamp_text, day):
    manual, method = build_manuals(stamp_text, day)
    return {"report": build_report(results, week, stamp_text, day), "manual": manual, "method": method}


def write(results, week, folder=db.OUT):
    text, day, _, warnings = stamp()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for kind, page in build(results, week, text, day).items():
        (folder / FILES[kind]).write_text(page, encoding="utf-8")
    for name, data in assets().items():
        (folder / name).write_bytes(data)
    if folder == PUBLISHED and text.endswith("+"):
        warnings.append("the published copy names unsaved changes; commit the code, then rerun --pages and commit docs/")
    return warnings
