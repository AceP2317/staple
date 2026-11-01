"""The one-page report, out/report.html, built from the metric queries' results and nothing else.

Every figure on the page comes from a query in sql/metrics/, so the page cannot state a number the
queries do not. It loads nothing from the network, carries the house build stamp, and labels every chart
as made-up data.
"""
import html
import json
import subprocess
from pathlib import Path

from staple import db, dispatch, load, triage, world

ROOT = Path(__file__).resolve().parent.parent
PAGE = db.OUT / "report.html"
UNSTAMPED = "unstamped build"
# The author's house build stamp, read in place from its one copy and never re-implemented here. On a
# machine without it the page says "unstamped build" and the run prints why, rather than leaving it blank.
STAMP_MODULE = Path.home() / "dev" / "login-starter" / "lib" / "house" / "build-stamp.cjs"
SOURCES = ["staple/", "sql/", "data/", "run.py"]    # what the page's numbers are computed from
_STAMP_JS = ("const s=require(process.argv[1]);const r=s.readBuildStamp({cwd:process.argv[2]});"
             "const d=s.fileDays({cwd:process.argv[2],under:JSON.parse(process.argv[3])});"
             "const last=d?Object.values(d).sort().pop():null;"
             "process.stdout.write(JSON.stringify({text:r.text,warnings:r.warnings,re:s.STAMP_RE.source,"
             "day:last?s.prettyDay(last):null}))")
e = html.escape


def stamp(root=ROOT):
    """(stamp, the day the page's sources last changed, the stamp pattern, warnings)."""
    if not STAMP_MODULE.exists():
        return UNSTAMPED, None, None, [f"the house build stamp is not on this machine, so the page says '{UNSTAMPED}'"]
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


def rows(results, name):
    columns, data = results[name]
    return [dict(zip(columns, r)) for r in data]


def pct(v):
    return f"{v:.1f}%"


def tile(label, value, note):
    return (f'<div class="tile"><p class="label">{e(label)}</p><p class="value">{e(value)}</p>'
            f'<p class="note">{e(note)}</p></div>')


def bars(items):
    """Ranked horizontal bars in one hue; the row the story is about wears the accent. Each item is
    (label, value, shown, tip, kind), with kind "lead", "" or "control"."""
    top = max(v for _, v, *_ in items)
    return '<div class="bars">' + "".join(
        f'<div class="bar-row {kind}" tabindex="0" data-tip="{e(tip)}"><span class="bar-label">{e(label)}</span>'
        f'<span class="track"><span class="bar" style="--v:{value / top:.3f}"></span>'
        f'<span class="bar-value">{e(shown)}</span></span></div>'
        for label, value, shown, tip, kind in items) + "</div>"


def figure(title, chart, result):
    columns, data = result
    head = "".join(f"<th>{e(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in r) + "</tr>" for r in data)
    return (f'<figure><figcaption>{e(title)} <span class="madeup">made-up data</span></figcaption>{chart}'
            f'<details{"" if chart else " open"}><summary>Show the numbers</summary><div class="scroll"><table><thead><tr>{head}</tr>'
            f'</thead><tbody>{body}</tbody></table></div></details></figure>')


def curve(points):
    """Coverage against carry-list length: three lines, the axis carrying the values, a crosshair for hover."""
    w, h, left, right, top, bottom = 560, 300, 56, 18, 16, 48
    ks = [p["k"] for p in points]
    x = [left + i * (w - left - right) / (len(ks) - 1) for i in range(len(ks))]

    def y(v):
        return top + (100 - v) * (h - top - bottom) / 100

    series = [("predictor_pct", "Predictor", "--s1"), ("national_pct", "National top parts", "--s2"),
              ("shuffled_pct", "Shuffled history (control)", "--s3")]
    today = points[0]["todays_trucks_pct"]
    svg = [f'<line class="grid" x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
           f'<text class="tick" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v}%</text>' for v in (0, 25, 50, 75, 100)]
    svg += [f'<text class="tick" x="{xi:.1f}" y="{h - bottom + 20}" text-anchor="middle">{k}</text>' for xi, k in zip(x, ks)]
    svg.append(f'<text class="tick" x="{(left + w - right) / 2:.1f}" y="{h - 6}" text-anchor="middle">parts on the carry list</text>')
    svg.append(f'<line class="ref" x1="{left}" x2="{w - right}" y1="{y(today):.1f}" y2="{y(today):.1f}"/>'
               f'<text class="ref-label" x="{left + 8}" y="{y(today) - 7:.1f}">today\'s trucks: {pct(today)}</text>')
    for key, _, colour in series:
        line = " ".join(f"{xi:.1f},{y(p[key]):.1f}" for xi, p in zip(x, points))
        svg.append(f'<polyline class="series" style="stroke:var({colour})" points="{line}"/>'
                   f'<circle class="dot" style="fill:var({colour})" cx="{x[-1]:.1f}" cy="{y(points[-1][key]):.1f}" r="4"/>')
    lead = points[-1]["predictor_pct"]
    svg.append(f'<text class="end-label" x="{x[-1] - 10:.1f}" y="{y(lead) - 12:.1f}" text-anchor="end">'
               f'predictor, {ks[-1]} parts: {pct(lead)}</text>')
    svg.append(f'<line class="cross" x1="0" x2="0" y1="{top}" y2="{h - bottom}"/>'
               f'<rect class="hit" x="{left}" y="{top}" width="{w - left - right}" height="{h - top - bottom}"/>')
    data = {"w": w, "xs": x, "ks": ks, "today": today,
            "series": [{"name": name, "values": [p[key] for p in points]} for key, name, _ in series]}
    keys = "".join(f'<span class="key"><i style="background:var({c})"></i>{e(n)}</span>' for _, n, c in series)
    return (f'<div class="legend">{keys}</div><svg viewBox="0 0 {w} {h}" tabindex="0" data-curve="{e(json.dumps(data))}" '
            f'aria-label="First-visit coverage against carry-list length; the table below holds every value">{"".join(svg)}</svg>')


def build(results, stamp_text, day):
    names = [n for n, *_ in dispatch.POLICIES]
    disp = {r["policy"]: r for r in rows(results, "dispatch_scorecard")}
    now, ours = disp[names[0]], disp[names[-1]]
    shuffled_skills, parts_only = disp[dispatch.SHUFFLED_SKILLS], disp[names[3]]
    best = {}
    for r in rows(results, "inventory_roi"):
        if r["policy"] not in best or r["cost_a_truck_day"] < best[r["policy"]]["cost_a_truck_day"]:
            best[r["policy"]] = r
    fixed, bal = best[load.TODAY], best[load.BALANCED]
    ai = {r["method"]: r for r in rows(results, "ai_step")}
    claude, keywords = ai["Claude"], ai["keyword rules"]
    causes = rows(results, "parts_caused_reschedules")
    share = 100 * sum(r["parts_caused"] for r in causes) / sum(r["repeat_visits"] for r in causes)
    worst = causes[0]
    points = rows(results, "carry_list_curve")
    three = next(p for p in points if p["k"] == load.SHOWN_K)
    stuck = rows(results, "backorder_exposure")

    tiles = "".join([
        tile("Fixed on the first visit", pct(ours["first_visit_fix_pct"]),
             f"from {pct(now['first_visit_fix_pct'])} sending the nearest technician on today's stock"),
        tile("Cost a call", f"${ours['cost_per_call']:.2f}",
             f"from ${now['cost_per_call']:.2f}: minutes at ${dispatch.MINUTE:.0f}, second visits at "
             f"${load.REPEAT_VISIT:.0f}, both made up"),
        tile("Jobs the truck covers", pct(bal["covered_pct"]),
             f"from {pct(fixed['covered_pct'])}, at ${bal['cost_a_truck_day']:.0f} a truck-day against ${fixed['cost_a_truck_day']:.0f}"),
        tile("Customer's words read right", pct(claude["agrees_booked_pct"]),
             f"agreement with the booked code, against {pct(keywords['agrees_booked_pct'])} for keyword rules")])

    cause_bars = bars([(r["appliance"], r["parts_caused_per_100_jobs"],
                        f"{r['parts_caused_per_100_jobs']:.1f} per 100 jobs",
                        f"{r['appliance']}: {r['parts_caused']:,} repeat visits for a missing part in {r['jobs']:,} jobs, "
                        f"{r['parts_share_of_repeats_pct']:.0f}% of its repeats", "lead" if r is worst else "")
                       for r in causes])
    load_rows = sorted(best.values(), key=lambda r: r["cost_a_truck_day"])
    load_bars = bars([(r["policy"] + (f", {r['parts_per_job']} per job" if r["policy"] in (load.TOP_UP, load.LISTS_ONLY) else ""),
                       r["cost_a_truck_day"], f"${r['cost_a_truck_day']:.0f} · covers {pct(r['covered_pct'])}",
                       f"{r['policy']}: ${r['cost_a_truck_day']:.2f} a truck-day, covers {pct(r['covered_pct'])}, "
                       f"${r['stock_value']:,.0f} of stock on board", "lead" if r is bal else "")
                      for r in load_rows])
    dispatch_bars = bars([(r["policy"], r["first_visit_fix_pct"],
                           f"{pct(r['first_visit_fix_pct'])} · ${r['cost_per_call']:.0f} a call",
                           f"{r['policy']}: fixed first visit {pct(r['first_visit_fix_pct'])}, {r['drive_minutes']} drive "
                           f"minutes and ${r['cost_per_call']:.2f} a call, {r['rolled']} rolled to tomorrow",
                           "lead" if r is ours else "control" if "control" in r["policy"] else "")
                          for r in disp.values()])
    # The follow-up variants are left to the table: their simulated customer answers with the booked code,
    # so their agreement with it is partly built in. Their honest measure is list coverage, in the text.
    shown = ["Claude", "keyword rules", next(m for m in ai if m.startswith("Claude answers shuffled"))]
    asked = ai["Claude, asking when unsure"]
    random_replies = next(r for m, r in ai.items() if m.startswith("Claude, asking when unsure, random"))
    ai_bars = bars([(m, ai[m]["agrees_booked_pct"],
                     f"{pct(ai[m]['agrees_booked_pct'])} · list covers {pct(ai[m]['coverage_pct'])}",
                     f"{m}: agrees with the booked code {pct(ai[m]['agrees_booked_pct'])}, with the true problem "
                     f"{pct(ai[m]['agrees_true_pct'])}; its 3-part list covers {pct(ai[m]['coverage_pct'])}",
                     "lead" if m == "Claude" else "control" if "control" in m else "") for m in shown])

    sections = [
        ("Where repeat visits come from",
         f"On today's trucks, a missing part causes {share:.0f}% of repeat visits. {worst['appliance'].capitalize()}s "
         f"are worst: {worst['parts_caused_per_100_jobs']:.0f} of every 100 {worst['appliance']} jobs need a second "
         "visit because a part was not on board.",
         figure("Repeat visits caused by a missing part, per 100 jobs", cause_bars, results["parts_caused_reschedules"])),
        ("Which parts to carry",
         "The predictor ranks parts by how often they fixed the same model family and problem, and leans on the wider "
         f"appliance group when a family's history is thin. Three parts cover {pct(three['predictor_pct'])} of held-back "
         f"jobs; the national top three cover {pct(three['national_pct'])}. Shuffling the history drops the predictor to "
         f"{pct(three['shuffled_pct'])}, so the gain comes from what the history links, not from the method. The list "
         "tops the truck up for the day; it does not replace the truck.",
         figure("Held-back jobs whose every needed part is on the list", curve(points), results["carry_list_curve"])),
        ("What to load tonight",
         "Each night the balanced truck ranks every part by its worth per unit of space: the booked calls' odds of "
         "needing it times a repeat visit, plus a share for calls that arrive unplanned, minus what holding it costs. "
         f"It covers {pct(bal['covered_pct'])} of jobs for ${bal['cost_a_truck_day']:.0f} a truck-day, against "
         f"{pct(fixed['covered_pct'])} and ${fixed['cost_a_truck_day']:.0f} for today's fixed stock, and it holds less: "
         f"${bal['stock_value']:,.0f} on board against ${fixed['stock_value']:,.0f}.",
         figure("Cost a truck-day: repeat visits, parts moved overnight, and money tied up", load_bars, results["inventory_roi"])),
        ("Who takes the call",
         "Every held-back day is replayed with the same made-up sick calls, breakdowns and priority changes. Staple sends "
         "each call to the technician with the lowest expected cost: drive and repair minutes, plus a second visit times "
         "the chance that technician's skill or truck fails it. Within each priority, the call with most to lose if its "
         f"best technician fills up goes first. Shuffling the technicians' skills drops it to "
         f"{pct(shuffled_skills['first_visit_fix_pct'])}, below the parts-only rule's {pct(parts_only['first_visit_fix_pct'])}, "
         "so the skill gain comes from knowing who is good at what.",
         figure("Calls fixed on the first visit, with a rolled call counted as not fixed", dispatch_bars, results["dispatch_scorecard"])),
        ("Reading the customer's words",
         f"Claude Haiku 5.5 ({triage.MODEL}) turns the customer's own words into a problem code with a confidence. "
         f"It agrees with the booked code {pct(claude['agrees_booked_pct'])} of the time, against "
         f"{pct(keywords['agrees_booked_pct'])} for keyword rules. When it is unsure it asks the customer one short "
         f"question, which lifts the 3-part list's coverage from {pct(claude['coverage_pct'])} to "
         f"{pct(asked['coverage_pct'])}; random replies to the same question drop it to "
         f"{pct(random_replies['coverage_pct'])}. Its answers are cached in the repo, so this page rebuilds without a key.",
         figure("Bookings where the reading agrees with the booked code", ai_bars, results["ai_step"])),
        ("Back-orders to chase first",
         "For each back-ordered part, the list forecasts the calls that will need it before it arrives, from training "
         "history only, and how many of those have no truck nearby holding one. Over the held-back months it expected "
         f"{sum(r['expected_calls'] for r in stuck):.0f} calls and {sum(r['expected_stuck'] for r in stuck):.0f} stuck; "
         f"the days then brought {sum(r['actual_calls'] for r in stuck)} and {sum(r['actual_stuck'] for r in stuck)}.",
         figure("Back-ordered parts, by repeat-visit dollars at risk before arrival", "", results["backorder_exposure"]))]
    body = "".join(f'<section><h2>{e(h)}</h2><p>{e(t)}</p>{f}</section>' for h, t, f in sections)
    stamp_line = (f'<p class="stamp"><span class="build-stamp">{e(stamp_text)}</span>'
                  f' · results last changed <span class="content-day">{e(day or "unknown")}</span></p>')
    world_line = (f"{world.JOBS:,} jobs over three years, {len(world.CATALOGUE)} appliance types, "
                  f"{world.TECHS} technicians")
    return (PAGE_TEMPLATE.replace("@@TILES@@", tiles).replace("@@BODY@@", body).replace("@@STAMP@@", stamp_line)
            .replace("@@WORLD@@", world_line))


def write(results, path=PAGE):
    text, day, _, warnings = stamp()
    Path(path).write_text(build(results, text, day), encoding="utf-8")
    return warnings


PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Staple report</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--dim:#c3c2b7;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--ring:rgba(11,11,11,.10)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--dim:#4a4a46;--s1:#3987e5;
--s2:#d95926;--s3:#199e70;--ring:rgba(255,255,255,.10)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--dim:#4a4a46;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--ring:rgba(255,255,255,.10)}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:880px;margin:0 auto;padding:32px 16px 48px}
h1{font-size:2rem;margin:0}h2{font-size:1.25rem;margin:0 0 8px}
.lede{color:var(--ink-2);margin:4px 0 0}.stamp{color:var(--muted);font-size:.8rem;margin:6px 0 0}
.madeup-note{border:1px solid var(--ring);background:var(--surface);border-radius:8px;padding:12px 14px;margin:20px 0;color:var(--ink-2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:20px 0 8px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:14px}
.tile p{margin:0}.tile .label{color:var(--ink-2);font-size:.85rem}.tile .value{font-size:2rem;font-weight:600}
.tile .note{color:var(--muted);font-size:.8rem}
section{margin:36px 0}section>p{color:var(--ink-2);margin:0 0 12px}
figure{margin:0;background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:14px}
figcaption{font-weight:600;font-size:.9rem;margin-bottom:10px}
.madeup{font-weight:400;color:var(--muted);font-size:.75rem;border:1px solid var(--ring);border-radius:4px;padding:0 5px;margin-left:4px}
.bars{display:grid;gap:6px}.bar-row{display:grid;grid-template-columns:minmax(9rem,16rem) 1fr;gap:10px;align-items:center;border-radius:4px;outline-offset:2px}
.bar-label{font-size:.85rem;color:var(--ink-2)}.track{display:flex;align-items:center;gap:8px;min-width:0}
.bar{height:16px;width:calc(var(--v)*66%);border-radius:0 4px 4px 0;background:var(--dim);flex:none}.lead .bar{background:var(--s1)}
.control .bar-label{color:var(--muted)}.lead .bar-label{color:var(--ink);font-weight:600}
.bar-value{font-size:.8rem;color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.bar-row:hover .bar,.bar-row:focus .bar{filter:brightness(1.12)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.8rem;color:var(--ink-2);margin-bottom:6px}
.key i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:6px}
svg{width:100%;height:auto;display:block}.grid{stroke:var(--grid);stroke-width:1}.tick{fill:var(--muted);font-size:12px}
.ref{stroke:var(--ink-2);stroke-width:1}.ref-label,.end-label{fill:var(--ink-2);font-size:12px}
.series{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.dot{stroke:var(--surface);stroke-width:2}
.cross{stroke:var(--muted);stroke-width:1;opacity:0}.hit{fill:transparent}
details{margin-top:10px;font-size:.85rem}summary{cursor:pointer;color:var(--ink-2)}
.scroll{overflow-x:auto}table{border-collapse:collapse;margin-top:8px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid var(--grid);white-space:nowrap}th{color:var(--ink-2);font-weight:600}
#tip{position:fixed;z-index:9;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--ring);
border-radius:6px;padding:6px 9px;font-size:.8rem;white-space:pre-line;box-shadow:0 2px 10px rgba(0,0,0,.12);max-width:320px}
footer{color:var(--muted);font-size:.8rem;border-top:1px solid var(--grid);padding-top:14px}
@media (max-width:560px){.bar-row{grid-template-columns:1fr;gap:2px}.bar{width:calc(var(--v)*45%)}
.bar-value{white-space:normal;flex:1 1 0;min-width:0}
.tick,.ref-label,.end-label{font-size:19px}}
</style></head><body><main>
<header><h1>Staple</h1><p class="lede">Which parts a technician should carry, and which technician should take the call.</p>@@STAMP@@</header>
<p class="madeup-note">Every number on this page comes from repair history I invented for this demo: @@WORLD@@, with wrong
descriptions, thin model histories and patternless failures planted on purpose. The method is real and every gain has a control that can withdraw it. The results are not, and the same back-test on
real history is the first thing I would run.</p>
<div class="tiles">@@TILES@@</div>
@@BODY@@
<section><h2>What this shows, and what it does not</h2><p>It shows the method can be checked: every result here reproduces
from one seed, and each gain has a shuffled control that falls back when the history's links are broken. It does not show the
gains, because the history is invented. On real history I would run the same back-test first, then a one-region pilot against
the current way of loading trucks. Three corners are cut on purpose: counting instead of a trained model, zone-to-zone drive
times instead of road routing, and a call-by-call night plan instead of a solver.</p></section>
<footer>Built with Python's standard library and SQLite; every number on this page comes from the queries in sql/metrics/.
Designed and directed by Ian Provencher, built with Claude Code.</footer>
</main><div id="tip" hidden></div>
<script>
const tip=document.getElementById('tip');
function show(text,el,x,y){tip.textContent=text;tip.hidden=false;const r=el.getBoundingClientRect();
let left=x??r.left,top=(y??r.top)-tip.offsetHeight-8;if(top<4)top=(y??r.bottom)+12;
tip.style.left=Math.max(4,Math.min(left,innerWidth-tip.offsetWidth-8))+'px';tip.style.top=top+'px'}
function hide(){tip.hidden=true;document.querySelectorAll('.cross').forEach(c=>c.style.opacity=0)}
document.querySelectorAll('[data-tip]').forEach(el=>{const on=()=>show(el.dataset.tip,el);
el.addEventListener('pointerenter',on);el.addEventListener('focus',on);el.addEventListener('pointerleave',hide);el.addEventListener('blur',hide)});
document.querySelectorAll('svg[data-curve]').forEach(svg=>{const d=JSON.parse(svg.dataset.curve),cross=svg.querySelector('.cross');let i=2;
const at=j=>{cross.setAttribute('x1',d.xs[j]);cross.setAttribute('x2',d.xs[j]);cross.style.opacity=1;const k=d.ks[j];
return k+' part'+(k>1?'s':'')+' on the list\\n'+d.series.map(s=>s.values[j].toFixed(1)+'%  '+s.name).join('\\n')+'\\n'+d.today.toFixed(1)+"%  today's trucks"};
svg.addEventListener('pointermove',ev=>{const r=svg.getBoundingClientRect(),xv=(ev.clientX-r.left)/r.width*d.w;
i=d.xs.reduce((b,x,j)=>Math.abs(x-xv)<Math.abs(d.xs[b]-xv)?j:b,0);show(at(i),svg,ev.clientX+12,ev.clientY)});
svg.addEventListener('pointerleave',hide);svg.addEventListener('focus',()=>show(at(i),svg));svg.addEventListener('blur',hide);
svg.addEventListener('keydown',ev=>{if(ev.key!=='ArrowRight'&&ev.key!=='ArrowLeft')return;
i=Math.max(0,Math.min(d.ks.length-1,i+(ev.key==='ArrowRight'?1:-1)));show(at(i),svg);ev.preventDefault()})});
</script></body></html>
"""
