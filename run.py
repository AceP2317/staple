"""The one command: `python run.py` builds the made-up world and every result from it, into out/.
`python run.py --live` asks the Claude API afresh for the AI step, with your own ANTHROPIC_API_KEY.
`python run.py --pages` also writes the four pages and languages.json to docs/, the copy the repository publishes.
`python run.py --card` only photographs the share card with Chrome; run it when the card's words or colors change.
The report is the week's plan (staple/plan.py) beside the 92-day evidence behind it."""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from staple import backorders, backtest, db, dispatch, load, metrics, plan, report, triage, world

CHROMES = ["chrome", "google-chrome", "chromium", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]


def card():
    """Photographs the share card page at its exact size with headless Chrome, the one step not in Python."""
    chrome = next((c for c in (shutil.which(n) or (n if Path(n).exists() else None) for n in CHROMES) if c), None)
    if not chrome:
        sys.exit("no Chrome found, so the share card was not photographed; looked for: " + ", ".join(CHROMES))
    db.OUT.mkdir(parents=True, exist_ok=True)
    page, shot = db.OUT / "card.html", report.PAGES / report.CARD
    page.write_text(report.card_html(), encoding="utf-8")
    w, h = report.CARD_SIZE
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--user-data-dir={profile}", f"--window-size={w},{h}", f"--screenshot={shot.resolve()}",
                        page.resolve().as_uri()], check=True, capture_output=True, timeout=120)
    (report.PAGES / "card.sha").write_text(report.card_fingerprint() + "\n", encoding="utf-8")
    print(f"share card: {shot}, photographed from {page}; bump report.CARD_VERSION if its pixels changed")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")    # a piped run on Windows would otherwise garble the stamp's middle dot
    report.notes_in_plain_words()
    unknown = [a for a in sys.argv[1:] if a not in ("--live", "--pages", "--card")]
    if unknown:
        sys.exit(f"run.py does not know {', '.join(unknown)}; it takes --live, --pages or --card, or nothing")
    if "--card" in sys.argv[1:]:
        return card()
    started = time.perf_counter()
    conn = db.fresh()
    world.build(conn)
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"world: {jobs:,} made-up repair jobs written to {db.DB_PATH}")
    bt = backtest.run(conn)
    k = backtest.SHOWN_K
    print(f"back-test on {bt['jobs']:,} held-back jobs, coverage with {k} parts on the list:")
    for name, row in bt["coverage"].items():
        print(f"  {name:<56} {row[k]:.1%}")
    print(f"  {'today' + chr(39) + 's trucks':<56} {bt['today']:.1%}")
    ld = load.run(conn)
    today = ld["today"]
    print(f"truck loads over {today['truck_days']:,} held-back truck-days, per truck-day (dollars made up):")
    for r in [today, ld["everything"], *ld["best"].values(), ld["balanced"], ld["control_balanced"], ld["equal_space"],
              *ld["moves"].values()]:
        label = r["policy"] + (f", {r['k']} per job" if r["policy"] in (load.TOP_UP, load.LISTS_ONLY) else "")
        print(f"  {label}: covers {r['coverage']:.1%}, {r['parts']:.0f} parts, "
              f"${r['value']:,.0f} of stock held, costs ${r['total']:,.2f} a truck-day")
    print(f"  the top-up pays while a repeat visit costs more than ${ld['break_even']:.2f}")
    av = ld["availability"]
    print(f"  {av['blocked']:,} jobs needed a part no truck nearby holds; the odds predicted {av['expected']:.0f}; "
          f"the flag caught {av['caught']:,} of them in {av['flagged']:,} flags")
    stuck = backorders.run(conn)
    scored = [r for r in stuck if r["first"] and r["whole"]]
    print(f"fleet back-order list, made again for each held-back day: {len(stuck):,} part-days; the "
          f"{len(scored)} back-orders that shipped inside those days, forecast against what came:")
    print(f"  calls before the part shipped: forecast {sum(r['expected'] for r in scored):.0f}, came "
          f"{sum(r['actual'] for r in scored)}; with no truck nearby holding one: forecast "
          f"{sum(r['stuck'] for r in scored):.0f}, came {sum(r['actual_stuck'] for r in scored)}")
    for r in stuck[:3]:
        print(f"  {r['day']}, {r['part']}: ships in {plan.days(r['days_left'])}, {r['expected']:.1f} calls expected, "
              f"{r['stuck']:.1f} with no truck nearby holding one (${r['at_risk']:,.0f})")
    # Loaded once, so a live run's answers reach the scores and the week's plan alike.
    answers, followups = triage.load(conn, live="--live" in sys.argv[1:])
    tr = triage.run(conn, answers, followups)
    print(f"AI step ({triage.MODEL}) reading the customer's own words, on the same held-back jobs, which say their "
          f"problems {tr['phrasings']} ways:")
    for name, r in tr["rows"].items():
        print(f"  {name:<56} agrees with the booked code {r['agree_booked']:.1%}, with the true problem "
              f"{r['agree_true']:.1%}; 3-part list covers {r['coverage']:.1%}")
    print(f"  follow-up question asked on {tr['asked']:,} unsure bookings; the customer's problem was among "
          f"the offered answers on {tr['settled']:,}")
    for b in tr["bands"]:
        print(f"  stated confidence {b['low']:.2f} to {b['high']:.2f}: {b['jobs']:,} bookings in {b['phrasings']} "
              f"phrasings, first reading matches the booked code {b['agree_booked']:.1%}")
    dp = dispatch.run(conn)
    print("dispatch replay of every held-back day, with the same made-up sick calls, breakdowns and priority changes:")
    for name, r in dp.items():
        print(f"  {name:<52} first-time fix {r['fixes_per_call']:.1%} of calls, {r['drive_per_call']:.1f} drive "
              f"minutes a call, ${r['cost_per_call']:.2f} a call")
    found = metrics.run(conn)
    print(f"metrics: {len(found)} SQL files in sql/metrics/, each result written to out/metrics/<name>.csv")
    week = plan.build(conn, answers, followups)
    print(f"the week's plan, {plan.WEEK} days coded by Claude and replayed; first-time fix against the nearest technician:")
    for d in week["days"]:
        o, b, m = d["ours"], d["base"], d["morning"]
        print(f"  {d['day']}: {o['fixes'] / o['calls']:.1%} against {b['fixes'] / b['calls']:.1%}; Claude asked "
              f"{d['ai']['asked']} of {d['ai']['coded']}; {len(m['sick']) or 'none'} sick, "
              f"{plan.counted(len(m['broken']), 'breakdown')}, {plan.counted(len(m['moved']), 'call')} moved; "
              f"{len(d['todo'])} to-do items")
    for folder in [db.OUT] + ([report.PUBLISHED] if "--pages" in sys.argv[1:] else []):
        for warning in report.write(found, week, folder):
            print(f"  WARN build stamp: {warning}")
        print(f"pages: {', '.join(str(folder / f) for f in report.FILES.values())}")
    print(f"done in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
