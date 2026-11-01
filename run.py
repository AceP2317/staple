"""The one command: `python run.py` builds the made-up world and every result from it, into out/.
`python run.py --live` asks the Claude API afresh for the AI step, with your own ANTHROPIC_API_KEY.
`python run.py --pages` also writes the three pages to docs/, the copy the repository publishes."""
import sys
import time

from staple import backorders, backtest, db, dispatch, load, metrics, report, triage, world


def main():
    started = time.perf_counter()
    conn = db.fresh()
    world.build(conn)
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"world: {jobs:,} made-up repair jobs written to {db.DB_PATH}")
    bt = backtest.run(conn)
    k = backtest.SHOWN_K
    print(f"back-test on {bt['jobs']:,} held-back jobs, first-visit coverage with {k} parts on the list:")
    for name, row in bt["coverage"].items():
        print(f"  {name:<26} {row[k]:.1%}")
    print(f"  {'today' + chr(39) + 's trucks':<26} {bt['today']:.1%}")
    ld = load.run(conn)
    today = ld["today"]
    print(f"truck loads over {today['truck_days']:,} held-back truck-days, per truck-day (dollars made up):")
    for r in [today, ld["everything"], *ld["best"].values(), ld["balanced"]]:
        label = r["policy"] + (f", {r['k']} per job" if r["policy"] in (load.TOP_UP, load.LISTS_ONLY) else "")
        print(f"  {label}: covers {r['coverage']:.1%}, {r['parts']:.0f} parts, "
              f"${r['value']:,.0f} of stock held, costs ${r['total']:,.0f} a day")
    print(f"  the top-up pays while a repeat visit costs more than ${ld['break_even']:.2f}")
    av = ld["availability"]
    print(f"  {av['blocked']:,} jobs needed a part no truck nearby holds; the odds predicted {av['expected']:.0f}; "
          f"the flag caught {av['caught']:,} of them in {av['flagged']:,} flags")
    stuck = backorders.run(conn)
    print(f"fleet back-order list, {len(stuck)} parts, top three by repeat-visit dollars at risk before arrival:")
    for r in stuck[:3]:
        print(f"  {r['part']}: arrives in {r['eta']} days, {r['expected']:.1f} calls expected, "
              f"{r['stuck']:.1f} with no truck nearby holding one (${r['at_risk']:,.0f})")
    tr = triage.run(conn, *triage.load(conn, live="--live" in sys.argv[1:]))
    print(f"AI step ({triage.MODEL}) reading the customer's own words, on the same held-back jobs:")
    for name, r in tr["rows"].items():
        print(f"  {name:<52} agrees with the booked code {r['agree_booked']:.1%}, with the true problem "
              f"{r['agree_true']:.1%}; 3-part list covers {r['coverage']:.1%}")
    print(f"  follow-up question asked on {tr['asked']:,} unsure bookings; the customer's problem was among "
          f"the offered answers on {tr['settled']:,}")
    dp = dispatch.run(conn)
    print("dispatch replay of every held-back day, with the same made-up sick calls, breakdowns and priority changes:")
    for name, r in dp.items():
        print(f"  {name:<52} first-visit fix {r['fixes_per_call']:.1%} of calls, {r['drive_per_call']:.0f} drive "
              f"minutes a call, ${r['cost_per_call']:.0f} a call")
    found = metrics.run(conn)
    print(f"metrics: {len(found)} SQL files in sql/metrics/, each result written to out/metrics/<name>.csv")
    for folder in [db.OUT] + ([report.PUBLISHED] if "--pages" in sys.argv[1:] else []):
        for warning in report.write(found, folder):
            print(f"  WARN build stamp: {warning}")
        print(f"pages: {', '.join(str(folder / f) for f in report.FILES.values())}")
    print(f"done in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
