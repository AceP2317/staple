"""The one command: `python run.py` builds the made-up world and every result from it, into out/."""
import time

from staple import db, world


def main():
    started = time.perf_counter()
    conn = db.fresh()
    world.build(conn)
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"world: {jobs:,} made-up repair jobs written to {db.DB_PATH}")
    print(f"done in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
