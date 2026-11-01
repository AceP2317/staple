"""The one command: `python run.py` builds the made-up world and every result from it, into out/."""
import time

from staple import backtest, db, world


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
    print(f"done in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
