"""The predictor: for a booked repair, rank the parts most likely to fix it.

It counts what fixed past jobs like this one, at three levels: the same model family and problem,
then the appliance type and problem, then the appliance type alone. A level with little history
leans on the level above it. That lean is a pull of PULL pretend jobs at the wider level's odds,
so 3 real jobs barely move the answer and 3,000 real jobs decide it.
"""
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

PULL = 20


@dataclass
class Cell:
    """Past jobs at one level: how many there were, and how many needed each part."""
    jobs: int = 0
    parts: Counter = field(default_factory=Counter)


class Predictor:
    def __init__(self, conn, pull=PULL, shuffled=False, seed=0):
        self.pull = pull
        self.parts_of = defaultdict(list)
        for part, appliance in conn.execute("SELECT part, type FROM parts ORDER BY part"):
            self.parts_of[appliance].append(part)

        needed = defaultdict(set)
        for job, part in conn.execute(
                "SELECT jp.job, jp.part FROM job_parts jp JOIN jobs j USING (job) WHERE j.split = 'train'"):
            needed[job].add(part)
        jobs = conn.execute(
            "SELECT job, type, family, booked_problem FROM jobs WHERE split = 'train' ORDER BY job").fetchall()

        if shuffled:
            # The control: deal each job's parts to a random other job of the same appliance type.
            # The type-level shares survive; any link to family or problem is destroyed.
            rng = random.Random(seed)
            by_type = defaultdict(list)
            for job, appliance, _, _ in jobs:
                by_type[appliance].append(job)
            dealt = {}
            for appliance, ids in by_type.items():
                hands = [needed[j] for j in ids]
                rng.shuffle(hands)
                dealt.update(zip(ids, hands))
            needed = dealt

        self.levels = {"type": defaultdict(Cell), "problem": defaultdict(Cell), "family": defaultdict(Cell)}
        for job, appliance, family, problem in jobs:
            for level, key in (("type", appliance), ("problem", problem), ("family", (family, problem))):
                cell = self.levels[level][key]
                cell.jobs += 1
                cell.parts.update(needed[job])

    def history(self, level, key):
        """How many past jobs sit at this level, and how many needed each part."""
        cell = self.levels[level].get(key, Cell())
        return cell.jobs, cell.parts

    def odds(self, appliance, family, problem):
        """The share of past jobs like this one that needed each part, leaning wider where history is thin."""
        n_t, c_t = self.history("type", appliance)
        n_p, c_p = self.history("problem", problem)
        n_f, c_f = self.history("family", (family, problem))
        out = {}
        for part in self.parts_of[appliance]:
            p_type = c_t[part] / n_t if n_t else 0.0
            p_problem = (c_p[part] + self.pull * p_type) / (n_p + self.pull) if (n_p + self.pull) else p_type
            p_family = (c_f[part] + self.pull * p_problem) / (n_f + self.pull) if (n_f + self.pull) else p_problem
            out[part] = p_family
        return out

    def carry_list(self, appliance, family, problem, k):
        odds = self.odds(appliance, family, problem)
        return sorted(odds, key=lambda p: (-odds[p], p))[:k]


def national_top(predictor, appliance, k):
    """Today's stocking logic: the parts most often needed for the appliance type, whatever the problem."""
    n, counts = predictor.history("type", appliance)
    return sorted(predictor.parts_of[appliance], key=lambda p: (-counts[p], p))[:k]
