"""The AI step: Claude reads the customer's own words and picks the problem code, with a confidence. An unsure
answer leans on the appliance type's overall odds, as the predictor does when history is thin.

Claude sees only the appliance type and each code's label, never the made-up phrasings, and so does the
keyword matcher it is scored against. Answers ship cached in data/triage-cache.json, so a run needs no
network or key. `python -m staple.triage build` rebuilds the cache with Claude Code on a subscription;
`python run.py --live` asks the Claude API afresh with the reader's own ANTHROPIC_API_KEY.
"""
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from staple.predict import Predictor, national_top

MODEL = "claude-haiku-5-5"
EFFORT = "low"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
CACHE = Path(__file__).resolve().parent.parent / "data" / "triage-cache.json"
LIVE = Path(__file__).resolve().parent.parent / "out" / "triage-live.json"
BLEND_BELOW = 0.7      # an answer less sure than this leans on the appliance type's overall odds
SHOWN_K = 3

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


def prompt(appliance, problems, phrases):
    codes = "\n".join(f"{code}: {label}" for code, label in problems)
    lines = "\n".join(f"{i}. {p}" for i, p in enumerate(phrases))
    return TEMPLATE.format(appliance=appliance, codes=codes, lines=lines)


def parse(text, problems, phrases):
    """The answer array, checked whole: one answer per phrase, known codes, a confidence in range."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("no JSON array in the answer")
    items = json.loads(text[start:end + 1])
    known = {code for code, _ in problems}
    by_i = {item["i"]: item for item in items}
    if sorted(by_i) != list(range(len(phrases))):
        raise ValueError(f"answers for {len(by_i)} of {len(phrases)} phrases")
    out = {}
    for i, words in enumerate(phrases):
        item = by_i[i]
        if item["code"] not in known or not 0 <= float(item["confidence"]) <= 1:
            raise ValueError(f"phrase {i}: an unknown code or a confidence out of range: {item}")
        out[words] = {"code": item["code"], "confidence": float(item["confidence"])}
    return out


def ask_claude_code(text):
    """One prompt through Claude Code on the operator's subscription, with every personal setting switched off."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    run = subprocess.run([shutil.which("claude") or "claude", "-p", "--safe-mode", "--model", MODEL, "--effort", EFFORT,
                          "--output-format", "json", "--tools", "", "--no-session-persistence",
                          "--system-prompt", SYSTEM],
                         input=text, capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
    reply = json.loads(run.stdout)
    if reply.get("is_error"):
        raise RuntimeError(f"Claude Code reported an error: {reply.get('result')}")
    return reply["result"]


def ask_api(text, key, url=API_URL):
    """One prompt through the Claude API, standard library only, with the reader's own key."""
    body = {"model": MODEL, "max_tokens": 16000, "system": SYSTEM, "output_config": {"effort": EFFORT},
            "messages": [{"role": "user", "content": text}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION})
    with urllib.request.urlopen(request, timeout=600) as response:
        reply = json.load(response)
    if reply.get("stop_reason") == "refusal":
        raise RuntimeError(f"Claude declined the request: {reply.get('stop_details')}")
    return "".join(block["text"] for block in reply["content"] if block["type"] == "text")


def ask_all(conn, ask):
    problems, phrases = problems_of(conn), phrases_of(conn)
    return {a: parse(ask(prompt(a, problems[a], phrases[a])), problems[a], phrases[a]) for a in sorted(phrases)}


def build(conn, path=CACHE):
    answers = ask_all(conn, ask_claude_code)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"model": MODEL, "effort": EFFORT, "prompt_id": PROMPT_ID, "answers": answers},
                               indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return answers


def load_answers(conn, live=False):
    """The cached answers, or fresh ones from the Claude API when live is asked for."""
    if live:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("--live needs ANTHROPIC_API_KEY set to your own Claude API key")
        answers = ask_all(conn, lambda text: ask_api(text, key))
        LIVE.write_text(json.dumps(answers, indent=1, sort_keys=True), encoding="utf-8")
        return answers
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    if cache["prompt_id"] != PROMPT_ID:
        raise SystemExit("data/triage-cache.json was built from a different prompt: run python -m staple.triage build")
    return cache["answers"]


def stem(word):
    for suffix in ("ing", "er", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[:-len(suffix)]
    return word


def words_of(text):
    return {stem(w) for w in re.findall(r"[a-z]+", text.lower())} - {"a", "an", "the", "at", "on", "in", "up", "and"}


def keyword(problems, words):
    """Today's rules: the code whose label shares the most words with the customer's, or none."""
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


def run(conn, jobs, answers, seed=7):
    predictor, problems = Predictor(conn), problems_of(conn)
    rng, by_type = random.Random(seed), {}
    for a, table in answers.items():
        values = list(table.values())
        rng.shuffle(values)
        by_type[a] = dict(zip(table, values))
    readings = {
        "booked code": lambda j: (j["problem"].split(".")[1], 1.0),
        "Claude": lambda j: reading(answers[j["type"]][j["words"]]),
        "Claude, top guess only": lambda j: (answers[j["type"]][j["words"]]["code"], 1.0),
        "keyword rules": lambda j: (keyword(problems[j["type"]], j["words"]), 1.0),
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
    calibration = {"sure": (len(sure), right(sure) if sure else 0.0), "unsure": (len(unsure), right(unsure) if unsure else 0.0)}
    conn.execute("DELETE FROM triage")
    conn.executemany("INSERT INTO triage VALUES (?,?,?,?,?)",
                     [(r["method"], r["jobs"], r["agree_booked"], r["agree_true"], r["coverage"]) for r in rows])
    conn.commit()
    return {"rows": {r["method"]: r for r in rows}, "calibration": calibration}


if __name__ == "__main__":
    from staple import db
    if sys.argv[1:] != ["build"]:
        raise SystemExit("usage: python -m staple.triage build")
    built = build(db.connect())
    print(f"cached {sum(len(v) for v in built.values())} answers from {MODEL} into {CACHE}")
