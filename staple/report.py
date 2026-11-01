"""Three static pages: the report (index.html), its instruction manual and its methods manual.

Every figure on the report comes from a query in sql/metrics/, and every constant the manuals quote is read
from the code that uses it, so no page can state a number the code does not. The pages load nothing from the
network, carry the house build stamp, label every chart as made-up data, and give nearly every element a
hover hint with the data behind it.
"""
import html
import json
import re
import subprocess
from pathlib import Path

from staple import db, dispatch, load, metrics, predict, triage, world

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(__file__).resolve().parent / "pages"
FILES = {"report": "index.html", "manual": "manual.html", "method": "method.html"}
PUBLISHED = ROOT / "docs"      # the copy the repository serves as its web page; `python run.py --pages` writes it
UNSTAMPED = "unstamped build"
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
    "back-order": "A part the distribution centres cannot ship until a set day.",
    "booked code": "The problem code the booking recorded from the customer's words. Real history knows only this code.",
    "true problem": "What was really wrong. Known here only because the history is invented.",
    "unplanned call": f"A call added or moved on the day, {load.UNPLANNED:.0%} of calls (made up). No night-before list saw it.",
    "rolled call": "A call no technician's day had room for. It moves to tomorrow and counts as not fixed today.",
    "certified technician": "A technician allowed to carry and fit locked parts.",
    "locked part": "A part that may ride only on a certified technician's truck.",
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
    "part": "The back-ordered part.", "eta_days": "Days until the centres can ship it.",
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
    "from a centre overnight": ("A centre can load it before the visit.", "Load it tonight."),
    "on another truck nearby": (f"A truck based within {load.NEARBY_MINUTES} minutes holds it.", "Hand it over, or send that truck."),
    "back-ordered": ("No centre can ship it in time and no truck nearby holds it.", "Expedite, source elsewhere, or move a spare."),
}
CHECK_ABOUT = {
    "guards": "The code stays under its size ceiling, imports nothing outside Python's own library, and names no private name in a public file. Planted inputs must trip both size and import checks.",
    "banned-control": "A name planted in a scratch file must be caught by the names scan, and a longer word containing a name must not be.",
    "predictor": "One well-populated and one thin cell are recomputed by hand from the three levels' counts. The thin cell must sit near the wider odds with the pull, and far from them without it.",
    "backtest": "Coverage on the held-back jobs. The predictor must beat the national list, and the lean must help the newest families.",
    "control": "The predictor trained on shuffled history must score no better than the national baseline.",
    "dollars": "Every load policy's costs, with planted loads recomputed by hand. No load may break the space, lock or back-order rules, and a planted bad load must show all three faults. The balanced truck must be the cheapest load, and the back-order flag must be calibrated.",
    "backorders": "The fleet back-order forecast against what the held-back days brought, within three Poisson spreads. A forecast three times too high must fail.",
    "triage": "The AI step on its cached answers, live mode against a local stand-in for the API, and its controls: shuffled answers and random replies must fall back.",
    "dispatch": "Every dispatch policy replayed. Staple must beat the nearest technician on fixes and on cost, and be cheapest on its own cost. Zero rule breaks, and a planted bad visit must show all four kinds.",
    "dispatch-control": "Shuffled skills must lose at least half the skill gain. Shuffled parts odds must stay within a point. No control may win by overbooking the day.",
    "sql": "Every metric query returns rows. The history metrics are recomputed in Python apart from the SQL, the ROI must match the replay, and planted changes in a copy must move each metric.",
    "report": "The pages carry the stamp, the results day, a made-up label and a table per chart, a hint on every element that needs one, and nothing from the network. The published copy must match a fresh build, and planted faults must be refused.",
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
    return f' data-tip="{e(text)}" tabindex="0"'


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
    for key, _, colour, _ in series:
        line = " ".join(f"{xi:.1f},{y(p[key]):.1f}" for xi, p in zip(x, points))
        svg.append(f'<polyline class="series" style="stroke:var({colour})" points="{line}"/>'
                   + "".join(f'<circle class="dot" style="fill:var({colour})" cx="{xi:.1f}" cy="{y(p[key]):.1f}" r="3.5"/>'
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


def shell(kind, title, lede, body, stamp_text, day):
    buttons = {"report": ["report", "method"], "manual": ["back", "method"], "method": ["back", "report"]}[kind]
    nav = "".join(f'<a class="btn{"" if i == 0 else " ghost"}" href="{NAV[b][1]}"{"" if b == "back" else " target=\"_blank\" rel=\"noopener\""}'
                  f'{tipattr(NAV[b][0] + chr(10) + NAV[b][2])}>{e(NAV[b][0])}</a>' for i, b in enumerate(buttons))
    css, js = (PAGES / "house.css").read_text(encoding="utf-8"), (PAGES / "tips.js").read_text(encoding="utf-8")
    h1_tip = "Staple\nA parts predictor, truck loads, a dispatch replay and an AI step, on invented repair history."
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(title)}</title><style>{css}</style></head><body><header class="band"><div class="band-in"><div>'
            f'<h1{tipattr(h1_tip)}>{e("Staple" if kind == "report" else title)}</h1><p class="lede">{e(lede)}</p>{stamp_line(stamp_text, day)}</div>'
            f'<nav class="actions">{nav}</nav></div></header><main>{body}'
            f'<footer>Built with Python\'s standard library and SQLite. Every number comes from the queries in '
            f'{hv("sql/metrics/", "sql/metrics/" + chr(10) + ", ".join(p.stem for p in sorted(metrics.SQL.glob("*.sql"))))}. '
            f'Designed and directed by Ian Provencher, built with Claude Code.</footer></main><div id="tip" hidden></div>'
            f'<script>{js}</script></body></html>\n')


def section(name, heading, text, fig):
    tip = f"{heading}\nSource: sql/metrics/{name}.sql. {about(name)}"
    return f'<section><h2{tipattr(tip)}>{e(heading)}</h2><p>{text}</p>{fig}</section>'


def build_report(results, stamp_text, day):
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
    causes = rows(results, "parts_caused_reschedules")
    fill = {r["appliance"]: r for r in rows(results, "truck_fill_rate")}
    repeats, caused, all_jobs = (sum(r[c] for r in causes) for c in ("repeat_visits", "parts_caused", "jobs"))
    worst = causes[0]
    points = rows(results, "carry_list_curve")
    three = next(p for p in points if p["k"] == load.SHOWN_K)
    stuck = rows(results, "backorder_exposure")
    R = load.REPEAT_VISIT

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
    shown = [claude, keywords, shuffled_ai]
    ai_bars = bars([(r["method"], r["agrees_booked_pct"], f"{pct(r['agrees_booked_pct'])} · list covers {pct(r['coverage_pct'])}",
                     f"{pct(r['agrees_booked_pct'])} of {r['jobs']:,} bookings match the booked code\n"
                     f"{pct(r['agrees_true_pct'])} match the true problem, known only because the data is invented.\n"
                     f"The 3-part list built from the reading covers {pct(r['coverage_pct'])} of jobs.",
                     "lead" if r is claude else "control" if "control" in r["method"] else "") for r in shown])

    def stuck_tip(r):
        return (f"{r['part']}: ships in {r['eta_days']} days\n{r['trucks_holding']} trucks hold one; {r['open_zones']} zones have "
                f"no holder within {load.NEARBY_MINUTES} minutes.\nForecast {r['expected_calls']} calls, {r['expected_stuck']} with no "
                f"truck nearby: ${r['dollars_at_risk']:,.0f} at risk.\nThe held-back days brought {r['actual_calls']} calls, "
                f"{r['actual_stuck']} stuck.")

    exp_calls, exp_stuck = sum(r["expected_calls"] for r in stuck), sum(r["expected_stuck"] for r in stuck)
    act_calls, act_stuck = sum(r["actual_calls"] for r in stuck), sum(r["actual_stuck"] for r in stuck)
    body = [
        f'<p class="hint-key">A dotted underline, or a pointer that turns into a question mark, means hover, tap or tab '
        f'there for the data behind it. A blue <span class="q"{tipattr(SAMPLE_Q)}>?</span> opens the manual at that word.</p>',
        join(Raw('<p class="note">'), "Every number on this page comes from repair history I invented for this demo: ",
             hv(f"{world.JOBS:,} jobs over three years", f"{world.JOBS:,} jobs over {world.DAYS:,} days\n{len(world.CATALOGUE)} appliance "
                f"types, {world.TECHS} technicians, {len(world.ZONES)} zones. The last {world.TEST_DAYS} days are held back for scoring."),
             ", with ", hv("wrong descriptions", f"{world.MISDESCRIBED:.0%} of bookings\nThe customer describes a different problem "
                           "than the real one, so the booked code is wrong."), ", ",
             hv("thin model histories", f"{world.RARE_FAMILIES} newest families per appliance\nIntroduced recently, so the predictor "
                "must lean on the wider group."), " and ",
             hv("patternless failures", f"{world.RANDOM_FAILURE:.0%} of jobs\nThe failed part follows no pattern, so no method can "
                "predict it."), " planted on purpose. The method is real and every gain has a control", q("control"),
             " that can withdraw it. The results are not, and the same back-test on real history is the first thing I would run.",
             Raw("</p>")),
        f'<div class="tiles">{tiles}</div>',
        section("parts_caused_reschedules", "Where repeat visits come from", join(
            "On today's trucks, a missing part causes ",
            hv(f"{100 * caused / repeats:.0f}%", f"{caused:,} of {repeats:,} repeat visits\nAcross {all_jobs:,} jobs. The rest failed "
               "on the technician's success rate with every part aboard."), " of repeat visits", q("repeat visit"), ". ",
            f"{worst['appliance'].capitalize()}s are worst: ",
            hv(f"{worst['parts_caused_per_100_jobs']:.0f} of every 100", f"{worst['parts_caused']:,} of {worst['jobs']:,} "
               f"{worst['appliance']} jobs\nOnly {pct(fill[worst['appliance']]['line_fill_pct'])} of the part lines they need "
               "are on the truck."), f" {worst['appliance']} jobs need a second visit because a part was not on board."),
            figure("parts_caused_reschedules", "Repeat visits caused by a missing part, per 100 jobs", cause_bars,
                   results["parts_caused_reschedules"])),
        section("carry_list_curve", "Which parts to carry", join(
            "The predictor ranks parts by how often they fixed the same model family and problem, and leans", q("lean"),
            " on the wider appliance group when a family's history is thin. Three parts on the carry list", q("carry list"),
            " cover ", hv(pct(three["predictor_pct"]), f"{pct(three['predictor_pct'])} of held-back jobs\nEvery needed part on a "
                          f"{load.SHOWN_K}-part list. With {points[-1]['k']} parts: {pct(points[-1]['predictor_pct'])}."),
            " of held-back jobs; the national top three cover ",
            hv(pct(three["national_pct"]), f"{pct(three['national_pct'])} with the national top {load.SHOWN_K}\nThe appliance's "
               "most-used parts, whatever the problem."), ". Shuffling the history drops the predictor to ",
            hv(pct(three["shuffled_pct"]), f"{pct(three['shuffled_pct'])} on shuffled history\nThe control falls to the national "
               "line, so the gain comes from what the history links."),
            ", so the gain comes from what the history links, not from the method. The list tops the truck up for the day; it "
            "does not replace the truck."),
            figure("carry_list_curve", "Held-back jobs whose every needed part is on the list", curve(points, calls),
                   results["carry_list_curve"])),
        section("inventory_roi", "What to load tonight", join(
            "Each night the balanced truck ranks every part by its worth per unit of space: the booked calls' odds of needing it "
            "times a repeat visit, plus a share for calls that arrive unplanned", q("unplanned call"), ", minus what holding it "
            "costs", q("holding cost"), ". It covers ",
            hv(pct(bal["covered_pct"]), f"{pct(bal['covered_pct'])} of jobs covered\nAgainst {pct(fixed['covered_pct'])} on today's trucks."),
            " of jobs for ", hv(f"${bal['cost_a_truck_day']:.0f} a truck-day", f"${bal['cost_a_truck_day']:.2f} a truck-day\nRepeat "
                               f"${bal['repeat_cost']:.2f}, moves ${bal['handling_cost']:.2f}, holding ${bal['holding_cost']:.2f}."),
            q("truck-day"), f", against {pct(fixed['covered_pct'])} and ",
            hv(f"${fixed['cost_a_truck_day']:.0f}", f"${fixed['cost_a_truck_day']:.2f} a truck-day on today's stock\nRepeat "
               f"${fixed['repeat_cost']:.2f}, moves ${fixed['handling_cost']:.2f}, holding ${fixed['holding_cost']:.2f}."),
            " for today's fixed stock, and it holds less: ",
            hv(f"${bal['stock_value']:,.0f}", f"${bal['stock_value']:,.0f} of stock on the balanced truck\n{bal['parts_aboard']:.0f} parts, "
               f"{bal['permanent_parts']:.0f} of them aboard nearly every day."),
            f" on board against ${fixed['stock_value']:,.0f}."),
            figure("inventory_roi", "Cost a truck-day: repeat visits, parts moved overnight, and money tied up", load_bars,
                   results["inventory_roi"])),
        section("dispatch_scorecard", "Who takes the call", join(
            "Every held-back day is replayed with the same made-up sick calls, breakdowns and priority changes. Staple sends "
            "each call to the technician with the lowest expected cost: drive and repair minutes, plus a second visit times the "
            "chance that technician's skill or truck fails it. Within each priority, the call with most to lose if its best "
            "technician fills up goes first", q("regret insertion"), ". Shuffling the technicians' skills drops it to ",
            hv(pct(shuffled_skills["first_visit_fix_pct"]), f"{pct(shuffled_skills['first_visit_fix_pct'])} with shuffled skills\n"
               f"Each technician read another's success rate and speed; ${shuffled_skills['cost_per_call']:.2f} a call."),
            ", below the parts-only rule's ",
            hv(pct(parts_only["first_visit_fix_pct"]), f"{pct(parts_only['first_visit_fix_pct'])} on parts alone\nEvery technician "
               f"taken as average; ${parts_only['cost_per_call']:.2f} a call."),
            ", so the skill gain comes from knowing who is good at what. A rolled call", q("rolled call"), " counts as not fixed."),
            figure("dispatch_scorecard", "Calls fixed on the first visit, with a rolled call counted as not fixed", dispatch_bars,
                   results["dispatch_scorecard"])),
        section("ai_step", "Reading the customer's words", join(
            f"Claude Haiku 5.5 ({triage.MODEL}) turns the customer's own words into a problem code with a confidence. It agrees "
            "with the booked code", q("booked code"), " ",
            hv(pct(claude["agrees_booked_pct"]), f"{pct(claude['agrees_booked_pct'])} agreement\nWith the true problem "
               f"{pct(claude['agrees_true_pct'])}, known only because the data is invented."),
            " of the time, against ", hv(pct(keywords["agrees_booked_pct"]), f"{pct(keywords['agrees_booked_pct'])} for keyword "
                                         "rules\nThe code whose label shares the most words with the customer's."),
            " for keyword rules. When it is unsure it asks the customer one short question, which lifts the 3-part list's "
            f"coverage from {pct(claude['coverage_pct'])} to ",
            hv(pct(asked["coverage_pct"]), f"{pct(asked['coverage_pct'])} with the question\nThe simulated customer picks the "
               "answer matching the booked problem."),
            "; random replies to the same question drop it to ",
            hv(pct(random_replies["coverage_pct"]), f"{pct(random_replies['coverage_pct'])} with random replies\nThe control: the "
               "question only helps when the answer is real."),
            ". Its answers are cached in the repo, so these pages rebuild without a key."),
            figure("ai_step", "Bookings where the reading agrees with the booked code", ai_bars, results["ai_step"])),
        section("backorder_exposure", "Back-orders to chase first", join(
            "For each back-ordered part", q("back-order"), ", the list forecasts the calls that will need it before it arrives, "
            "from training history only, and how many of those have no truck nearby holding one. Over the held-back months it "
            "expected ", hv(f"{exp_calls:.0f} calls and {exp_stuck:.0f} stuck", f"{exp_calls:.1f} calls, {exp_stuck:.1f} stuck\n"
                            f"${sum(r['dollars_at_risk'] for r in stuck):,.0f} at risk across {len(stuck)} parts."),
            "; the days then brought ", hv(f"{act_calls} and {act_stuck}", f"{act_calls} calls, {act_stuck} stuck\nCounted on the "
                                         "held-back days, which the forecast never saw."), "."),
            figure("backorder_exposure", "Back-ordered parts, by repeat-visit dollars at risk before arrival", "",
                   results["backorder_exposure"], row_tip=stuck_tip)),
        '<section><h2' + tipattr("What this shows, and what it does not\nThe instruction manual's last section lists every "
                                 "limit; the methods manual names each shortcut and its upgrade.") + '>What this shows, and what it '
        'does not</h2><p>It shows the method can be checked: every result here reproduces from one seed, and each gain has a '
        'shuffled control that falls back when the history\'s links are broken. It does not show the gains, because the '
        'history is invented. On real history I would run the same back-test first, then a one-region pilot against the '
        'current way of loading trucks. Three corners are cut on purpose: counting instead of a trained model, zone-to-zone '
        'drive times instead of road routing, and a call-by-call night plan instead of a solver.</p></section>']
    return shell("report", "Staple report", "Which parts a technician should carry, and which technician should take the call.",
                 "".join(body), stamp_text, day)


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
         "FOLLOWUP_TEMPLATE": as_sent(triage.FOLLOWUP_TEMPLATE)}
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


def build(results, stamp_text, day):
    manual, method = build_manuals(stamp_text, day)
    return {"report": build_report(results, stamp_text, day), "manual": manual, "method": method}


def write(results, folder=db.OUT):
    text, day, _, warnings = stamp()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for kind, page in build(results, text, day).items():
        (folder / FILES[kind]).write_text(page, encoding="utf-8")
    if folder == PUBLISHED and text.endswith("+"):
        warnings.append("the published copy names unsaved changes; commit the code, then rerun --pages and commit docs/")
    return warnings
