"""The one command: `python run.py` builds the made-up world and every result from it, into out/."""
import time

from staple import backtest, db, load, world


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
    print(f"  {'today' + chr(39) + 's trucks (6-8 parts per type)':<26} {bt['today']:.1%}")
    ld = load.run(conn, backtest.test_jobs(conn))
    today = ld["today"]
    print(f"truck loads over {today['truck_days']:,} held-back truck-days, per truck-day (dollars made up):")
    for r in [today, ld["everything"], *ld["best"].values()]:
        label = r["policy"] + (f", {r['k']} per job" if r["policy"] in (load.TOP_UP, load.LISTS_ONLY) else "")
        print(f"  {label}: covers {r['coverage']:.1%}, {r['parts']:.0f} parts, "
              f"${r['value']:,.0f} on board, ${r['total']:,.0f} a day")
    print(f"  the top-up pays while a repeat visit costs more than ${ld['break_even']:.2f}")
    av = ld["availability"]
    print(f"  back-order flag: {av['flagged']:,} jobs flagged, {av['caught']:,} of {av['blocked']:,} blocked jobs caught")
    print(f"done in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
