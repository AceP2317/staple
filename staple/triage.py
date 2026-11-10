"""The AI step: Claude reads the customer's own words and picks the problem code, with a confidence. An unsure
answer leans on the appliance type's overall odds, as the predictor does when history is thin.

Claude sees only the appliance type and each code's label, never the made-up phrasings, and so does the
keyword matcher it is scored against. When Claude is unsure, it writes one short question for the customer,
each answer pointing to one problem code. Answers ship cached in data/, so a run needs no network or key.

What the scores are, said once. The made-up world says each problem a small number of ways, so a score over
the held-back bookings is a score over those few phrasings, weighted by how often each is said. The customer
who answers Claude's question is simulated and always picks the answer that matches the booking, so the
score after the question is an upper bound; the same question answered at random is its lower end.

`python -m staple.triage build` asks Claude Code, on a subscription, for every phrase the caches lack and
drops the ones the world no longer says (`rebuild` asks for all of them afresh; `followups` asks only for the
questions); `python run.py --live` asks the Claude API afresh with the reader's own ANTHROPIC_API_KEY.
"""
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from staple.backtest import test_jobs
from staple.predict import Predictor, national_top

MODEL = "claude-haiku-5-5"


def named(model):
    """A model id as people write its name: claude-haiku-5-5 is Claude Haiku 5.5."""
    maker, family, *version = model.split("-")
    return f"{maker.capitalize()} {family.capitalize()} {'.'.join(version)}"


MODEL_NAME = named(MODEL)
EFFORT = "low"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
CACHE = Path(__file__).resolve().parent.parent / "data" / "triage-cache.json"
FOLLOWUP_CACHE = CACHE.with_name("followup-cache.json")
LIVE = Path(__file__).resolve().parent.parent / "out" / "triage-live.json"
BLEND_BELOW = 0.7      # an answer less sure than this leans on the appliance type's overall odds
SHOWN_K = 3
BANDS = (0.9, 0.8, BLEND_BELOW, 0.5, 0.0)   # the lower edge of each band of stated confidence, highest first
ASKED = "Claude, asking when unsure, ideal replies (upper bound)"
RANDOM_REPLIES = "Claude, asking when unsure, random replies (control)"
KEYWORDS = "keyword matcher"

SYSTEM = "You read appliance repair bookings and choose the problem code that best fits the customer's own words."
TEMPLATE = """Appliance: {appliance}
Problem codes:
{codes}

For each numbered customer description below, choose the most likely problem code and your confidence
from 0 to 1 that it is right. When the words fit no single problem, say so with a low confidence.
Reply with only a JSON array, one object per description:
[{{"i": <number>, "code": "<CODE>", "confidence": <0 to 1>}}]

{lines}"""
PROMPT_ID = hashlib.sha256((MODEL + EFFORT + SYSTEM + TEMPLATE).encode()).hexdigest()[:12]

FOLLOWUP_SYSTEM = "You help call-takers book appliance repairs by asking the customer one short, plain question."
FOLLOWUP_TEMPLATE = """Appliance: {appliance}
Problem codes:
{codes}

Each numbered customer description below is too vague to pick one problem code. For each, write one
question to ask the customer, under 20 words, with two to four answers they can pick from. Each answer
must point to a different problem code.
Reply with only a JSON array, one object per description:
[{{"i": <number>, "question": "<question>", "answers": [{{"text": "<answer>", "code": "<CODE>"}}]}}]

{lines}"""
FOLLOWUP_ID = hashlib.sha256((MODEL + EFFORT + FOLLOWUP_SYSTEM + FOLLOWUP_TEMPLATE).encode()).hexdigest()[:12]


def problems_of(conn):
    out = {}
    for code, appliance, label in conn.execute("SELECT code, type, label FROM problems ORDER BY code"):
        out.setdefault(appliance, []).append((code.split(".", 1)[1], label))
    return out


def phrases_of(conn):
    out = {}
    for appliance, words in conn.execute("SELECT DISTINCT type, words FROM jobs ORDER BY type, words"):
        out.setdefault(appliance, []).append(words)
    return out


def prompt(appliance, problems, phrases, template=TEMPLATE):
    codes = "\n".join(f"{code}: {label}" for code, label in problems)
    lines = "\n".join(f"{i}. {p}" for i, p in enumerate(phrases))
    return template.format(appliance=appliance, codes=codes, lines=lines)


def numbered(text, phrases):
    """The reply's JSON array, keyed by phrase number, with exactly one entry per phrase."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("no JSON array in the answer")
    by_i = {item["i"]: item for item in json.loads(text[start:end + 1])}
    if sorted(by_i) != list(range(len(phrases))):
        raise ValueError(f"answers for {len(by_i)} of {len(phrases)} phrases")
    return by_i


def parse(text, problems, phrases):
    """The answer array, checked whole: one answer per phrase, known codes, a confidence in range."""
    by_i, known, out = numbered(text, phrases), {code for code, _ in problems}, {}
    for i, words in enumerate(phrases):
        item = by_i[i]
        if item["code"] not in known or not 0 <= float(item["confidence"]) <= 1:
            raise ValueError(f"phrase {i}: an unknown code or a confidence out of range: {item}")
        out[words] = {"code": item["code"], "confidence": float(item["confidence"])}
    return out


def parse_followups(text, problems, phrases):
    """One question per phrase, under 30 words, with two to four answers that each name a different known code."""
    by_i, known, out = numbered(text, phrases), {code for code, _ in problems}, {}
    for i, words in enumerate(phrases):
        item = by_i[i]
        codes = [a["code"] for a in item["answers"]]
        if not (0 < len(item["question"].split()) <= 30 and 2 <= len(codes) <= 4
                and len(set(codes)) == len(codes) and set(codes) <= known):
            raise ValueError(f"follow-up {i} breaks the format: {item}")
        out[words] = {"question": item["question"],
                      "answers": [{"text": a["text"], "code": a["code"]} for a in item["answers"]]}
    return out


def ask_claude_code(text, system=SYSTEM):
    """One prompt through Claude Code on the operator's subscription, with every personal setting switched off."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    run = subprocess.run([shutil.which("claude") or "claude", "-p", "--safe-mode", "--model", MODEL, "--effort", EFFORT,
                          "--output-format", "json", "--tools", "", "--no-session-persistence",
                          "--system-prompt", system],
                         input=text, capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
    reply = json.loads(run.stdout)
    if reply.get("is_error"):
        raise RuntimeError(f"Claude Code reported an error: {reply.get('result')}")
    return reply["result"]


class LiveError(Exception):
    """The Claude API could not be asked, or did not answer in the agreed form. The message is for a person."""


def ask_api(text, key, url=API_URL, system=SYSTEM):
    """One prompt through the Claude API, standard library only, with the reader's own key."""
    body = {"model": MODEL, "max_tokens": 16000, "system": system, "output_config": {"effort": EFFORT},
            "messages": [{"role": "user", "content": text}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as err:
        said = err.read().decode("utf-8", "replace")
        try:
            said = json.loads(said)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            said = said[:200]
        hint = {401: " Check that ANTHROPIC_API_KEY holds a working key.", 429: " The key's rate limit was reached; wait and run again.",
                529: " The API is overloaded; wait and run again."}.get(err.code, "")
        raise LiveError(f"the Claude API answered {err.code}: {said}.{hint}") from None
    except (urllib.error.URLError, TimeoutError) as err:
        raise LiveError(f"the Claude API could not be reached at {url}: {getattr(err, 'reason', err)}") from None
    except ValueError:
        raise LiveError("the Claude API sent a reply that is not JSON") from None
    if reply.get("stop_reason") == "refusal":
        raise LiveError(f"Claude declined the request: {reply.get('stop_details')}")
    if reply.get("stop_reason") == "max_tokens":
        raise LiveError("Claude's reply was cut off at the token limit before the answer ended")
    return "".join(block["text"] for block in reply.get("content", []) if block.get("type") == "text")


def ask_all(conn, ask, have=None):
    """Claude's reading of every phrase the world says. have, when given, holds answers made earlier: only the
    phrases it lacks are asked, and a phrase the world no longer says is dropped."""
    problems, phrases, out = problems_of(conn), phrases_of(conn), {}
    for a in sorted(phrases):
        kept = {w: x for w, x in (have or {}).get(a, {}).items() if w in set(phrases[a])}
        new = [w for w in phrases[a] if w not in kept]
        fresh = parse(ask(prompt(a, problems[a], new)), problems[a], new) if new else {}
        out[a] = {w: (kept[w] if w in kept else fresh[w]) for w in phrases[a]}
    return out


def unsure_phrases(answers):
    return {a: sorted(w for w, x in table.items() if x["confidence"] < BLEND_BELOW) for a, table in answers.items()}


def ask_followups(conn, answers, ask, have=None):
    """One question for every unsure phrase, asked only for the phrases have lacks."""
    problems, unsure, out = problems_of(conn), unsure_phrases(answers), {}
    for a in sorted(unsure):
        kept = {w: q for w, q in (have or {}).get(a, {}).items() if w in set(unsure[a])}
        new = [w for w in unsure[a] if w not in kept]
        fresh = parse_followups(ask(prompt(a, problems[a], new, FOLLOWUP_TEMPLATE)), problems[a], new) if new else {}
        if unsure[a]:
            out[a] = {w: (kept[w] if w in kept else fresh[w]) for w in unsure[a]}
    return out


def save(path, prompt_id, answers):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"model": MODEL, "effort": EFFORT, "prompt_id": prompt_id, "answers": answers},
                               indent=1, sort_keys=True) + "\n", encoding="utf-8")


def build(conn, how="build"):
    """Fill the two caches through Claude Code. build asks only for what a cache lacks, rebuild asks for every
    phrase afresh, and followups leaves the answers alone and asks only for the questions."""
    earlier = None if how == "rebuild" else cached(CACHE, PROMPT_ID)
    if how == "followups":
        answers = earlier
    else:
        answers = ask_all(conn, ask_claude_code, earlier)
        save(CACHE, PROMPT_ID, answers)
    asked = ask_followups(conn, answers, lambda text: ask_claude_code(text, FOLLOWUP_SYSTEM),
                          cached(FOLLOWUP_CACHE, FOLLOWUP_ID) if how == "build" else None)
    save(FOLLOWUP_CACHE, FOLLOWUP_ID, asked)
    return answers, asked


def cached(path, prompt_id):
    cache = json.loads(path.read_text(encoding="utf-8"))
    if cache["prompt_id"] != prompt_id:
        raise SystemExit(f"{path.name} was built from a different prompt: run python -m staple.triage build")
    return cache["answers"]


def ask_live(conn, key, url=API_URL):
    """Every answer and follow-up question asked afresh from the Claude API. A run that fails stops with one
    plain sentence saying why, never with a trace."""
    try:
        answers = ask_all(conn, lambda text: ask_api(text, key, url))
        asked = ask_followups(conn, answers, lambda text: ask_api(text, key, url, FOLLOWUP_SYSTEM))
    except LiveError as err:
        raise SystemExit(f"--live stopped: {err}") from None
    except (ValueError, KeyError, TypeError) as err:
        raise SystemExit(f"--live stopped: Claude's reply was not in the agreed form ({err})") from None
    return answers, asked


def load(conn, live=False):
    """The cached answers and follow-up questions, or fresh ones from the Claude API when live is asked for."""
    if not live:
        return cached(CACHE, PROMPT_ID), cached(FOLLOWUP_CACHE, FOLLOWUP_ID)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("--live needs ANTHROPIC_API_KEY set to your own Claude API key")
    answers, asked = ask_live(conn, key)
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    LIVE.write_text(json.dumps({"answers": answers, "followups": asked}, indent=1, sort_keys=True), encoding="utf-8")
    return answers, asked


def stem(word):
    for suffix in ("ing", "er", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[:-len(suffix)]
    return word


def words_of(text):
    return {stem(w) for w in re.findall(r"[a-z]+", text.lower())} - {"a", "an", "the", "at", "on", "in", "up", "and"}


def keyword(problems, words):
    """A plain keyword matcher: the code whose label shares the most words with the customer's, or none. It is
    the simplest reading there is, a floor for Claude to beat, and no claim about how any booking screen works."""
    said = words_of(words)
    scores = {code: len(words_of(label) & said) for code, label in problems}
    best = max(scores.values())
    return None if best == 0 else min(c for c, s in scores.items() if s == best)


def carry(predictor, job, code, confidence=1.0, k=SHOWN_K):
    """The carry list for one reading of the words; an unsure reading leans on the appliance type's odds."""
    if code is None:
        return national_top(predictor, job["type"], k)
    odds = predictor.odds(job["type"], job["family"], f"{job['type']}.{code}")
    if confidence < BLEND_BELOW:
        n, counts = predictor.history("type", job["type"])
        odds = {p: confidence * o + (1 - confidence) * counts[p] / n for p, o in odds.items()}
    return sorted(odds, key=lambda p: (-odds[p], p))[:k]


def reading(answer):
    return answer["code"], answer["confidence"]


def asked(answer, followup, job, reply):
    """The reading after the follow-up question, when one was asked: the code of the customer's reply, or the
    lean reading when none of the offered answers fits what the customer would say."""
    if answer["confidence"] >= BLEND_BELOW or followup is None:
        return reading(answer)
    picked = reply(job, followup["answers"])
    return (picked, 1.0) if picked else reading(answer)


def honest(job, offered):
    """The simulated customer's reply to a follow-up question: the offered answer that matches the problem
    they booked, or none when no offered answer fits. A real customer answers less surely than this, so a
    score that rests on it is an upper bound."""
    booked = job["problem"].split(".")[1]
    return next((a["code"] for a in offered if a["code"] == booked), None)


def run(conn, answers, followups):
    """Scores every reading on the whole held-back period and writes the triage table. It takes no job list,
    so no caller can leave the table holding a subset; score() scores a subset and writes nothing."""
    out = score(conn, test_jobs(conn), answers, followups)
    conn.execute("DELETE FROM triage")
    conn.execute("DELETE FROM triage_calibration")
    conn.executemany("INSERT INTO triage VALUES (?,?,?,?,?)",
                     [(r["method"], r["jobs"], r["agree_booked"], r["agree_true"], r["coverage"]) for r in out["rows"].values()])
    conn.executemany("INSERT INTO triage_calibration VALUES (?,?,?,?,?,?)",
                     [(b["low"], b["high"], b["phrasings"], b["jobs"], b["agree_booked"], b["agree_true"]) for b in out["bands"]])
    conn.commit()
    return out


def band(confidence):
    """The lower edge of the band a stated confidence falls in."""
    return next(low for low in BANDS if confidence >= low)


def calibration(jobs, answers):
    """How often Claude's first reading is right at each level of confidence it states. A band no booking falls
    in is left out."""
    found = {low: {"said": set(), "jobs": 0, "booked": 0, "true": 0} for low in BANDS}
    for j in jobs:
        answer = answers[j["type"]][j["words"]]
        b = found[band(answer["confidence"])]
        b["said"].add((j["type"], j["words"]))
        b["jobs"] += 1
        b["booked"] += answer["code"] == j["problem"].split(".")[1]
        b["true"] += answer["code"] == j["true"].split(".")[1]
    edges = dict(zip(BANDS, (1.0,) + BANDS))
    return [{"low": low, "high": edges[low], "phrasings": len(b["said"]), "jobs": b["jobs"],
             "agree_booked": b["booked"] / b["jobs"], "agree_true": b["true"] / b["jobs"]}
            for low, b in found.items() if b["jobs"]]


def score(conn, jobs, answers, followups, seed=7):
    predictor, problems = Predictor(conn), problems_of(conn)
    rng, by_type = random.Random(seed), {}
    for a, table in answers.items():
        values = list(table.values())
        rng.shuffle(values)
        by_type[a] = dict(zip(table, values))
    booked = lambda j: j["problem"].split(".")[1]
    at_random = lambda j, offered: random.Random(j["job"]).choice(offered)["code"]
    follow = lambda j: followups.get(j["type"], {}).get(j["words"])
    readings = {
        "booked code": lambda j: (j["problem"].split(".")[1], 1.0),
        "Claude": lambda j: reading(answers[j["type"]][j["words"]]),
        "Claude, top guess only": lambda j: (answers[j["type"]][j["words"]]["code"], 1.0),
        ASKED: lambda j: asked(answers[j["type"]][j["words"]], follow(j), j, honest),
        RANDOM_REPLIES: lambda j: asked(answers[j["type"]][j["words"]], follow(j), j, at_random),
        KEYWORDS: lambda j: (keyword(problems[j["type"]], j["words"]), 1.0),
        "no code": lambda j: (None, 1.0),
        "Claude answers shuffled (control)": lambda j: reading(by_type[j["type"]][j["words"]]),
    }
    rows = []
    for name, read in readings.items():
        agree = truth = covered = 0
        for j in jobs:
            code, confidence = read(j)
            agree += code == j["problem"].split(".")[1]
            truth += code == j["true"].split(".")[1]
            covered += j["needed"] <= set(carry(predictor, j, code, confidence))
        rows.append({"method": name, "jobs": len(jobs), "agree_booked": agree / len(jobs),
                     "agree_true": truth / len(jobs), "coverage": covered / len(jobs)})
    sure = [j for j in jobs if answers[j["type"]][j["words"]]["confidence"] >= BLEND_BELOW]
    unsure = [j for j in jobs if answers[j["type"]][j["words"]]["confidence"] < BLEND_BELOW]
    right = lambda js: sum(answers[j["type"]][j["words"]]["code"] == j["problem"].split(".")[1] for j in js) / len(js)
    offered = [j for j in unsure if follow(j)]
    settled = sum(1 for j in offered if honest(j, follow(j)["answers"]))
    return {"rows": {r["method"]: r for r in rows}, "bands": calibration(jobs, answers),
            "calibration": {"sure": (len(sure), right(sure) if sure else 0.0),
                            "unsure": (len(unsure), right(unsure) if unsure else 0.0)},
            "phrasings": len({(j["type"], j["words"]) for j in jobs}),
            "asked": len(offered), "settled": settled}


if __name__ == "__main__":
    from staple import db, world
    if sys.argv[1:] not in (["build"], ["rebuild"], ["followups"]):
        raise SystemExit("usage: python -m staple.triage build | rebuild | followups")
    made_up = db.connect(":memory:")     # the phrases come from the world itself, so no earlier run is needed
    world.build(made_up)
    answers, asked_ = build(made_up, sys.argv[1])
    print(f"cached {sum(len(v) for v in answers.values())} answers and "
          f"{sum(len(v) for v in asked_.values())} follow-up questions from {MODEL} into {CACHE.parent}")
