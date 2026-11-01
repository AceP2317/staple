"""The back-test: predict the last three months as if they had not happened yet, then score it.

The score is first-visit coverage: the share of held-back jobs where every part the job needed
was on the carry list. It stands in for parts-caused first-time fix; it cannot see a repair that
failed for another reason, such as a wrong diagnosis.
"""
from collections import defaultdict

from staple.predict import Predictor, national_top

KS = range(1, 9)
SHOWN_K = 3


def test_jobs(conn):
    needed = defaultdict(set)
    for job, part in conn.execute(
            "SELECT jp.job, jp.part FROM job_parts jp JOIN jobs j USING (job) WHERE j.split = 'test'"):
        needed[job].add(part)
    rows = conn.execute("""SELECT j.job, j.type, j.family, j.booked_problem, j.noise, j.parts_on_truck,
                                  f.first_day > '2026-01-01'
                           FROM jobs j JOIN families f USING (family) WHERE j.split = 'test' ORDER BY j.job""")
    return [{"job": r[0], "type": r[1], "family": r[2], "problem": r[3], "noise": r[4],
             "on_truck": r[5], "thin": bool(r[6]), "needed": needed[r[0]]} for r in rows]


def coverage(jobs, list_for, k):
    if not jobs:
        return 0.0
    return sum(1 for j in jobs if j["needed"] <= set(list_for(j, k))) / len(jobs)


def methods(conn):
    real = Predictor(conn)
    shuffled = Predictor(conn, shuffled=True, seed=7)
    no_pull = Predictor(conn, pull=0)
    return {
        "predictor": lambda j, k: real.carry_list(j["type"], j["family"], j["problem"], k),
        "national top parts": lambda j, k: national_top(real, j["type"], k),
        "shuffled-history control": lambda j, k: shuffled.carry_list(j["type"], j["family"], j["problem"], k),
        "no lean on wider groups": lambda j, k: no_pull.carry_list(j["type"], j["family"], j["problem"], k),
    }


def run(conn):
    jobs = test_jobs(conn)
    found = {name: {k: coverage(jobs, f, k) for k in KS} for name, f in methods(conn).items()}
    today = sum(j["on_truck"] for j in jobs) / len(jobs)

    groups = {
        "no planted noise": [j for j in jobs if j["noise"] == "none"],
        "customer misdescribed": [j for j in jobs if j["noise"] == "misdescribed"],
        "patternless failure": [j for j in jobs if j["noise"] == "random"],
        "newest model families": [j for j in jobs if j["thin"]],
        "established families": [j for j in jobs if not j["thin"]],
    }
    lists = methods(conn)
    by_group = {g: {m: (len(js), coverage(js, lists[m], SHOWN_K)) for m in ("predictor", "national top parts",
                                                                        "no lean on wider groups")}
                for g, js in groups.items()}

    conn.execute("DELETE FROM backtest")
    conn.execute("DELETE FROM backtest_groups")
    conn.executemany("INSERT INTO backtest VALUES (?,?,?)",
                     [(m, k, c) for m, row in found.items() for k, c in row.items()] + [("today's trucks", 0, today)])
    conn.executemany("INSERT INTO backtest_groups VALUES (?,?,?,?,?)",
                     [(g, m, SHOWN_K, n, c) for g, row in by_group.items() for m, (n, c) in row.items()])
    conn.commit()
    return {"jobs": len(jobs), "coverage": found, "today": today, "groups": by_group}
