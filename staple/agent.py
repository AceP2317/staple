"""Claude as the parts buyer: one conversation for each back-ordered part, its facts through lookups, and one
action committed.

The buyer's scorer prices four actions for every back-ordered part of every held-back day. Here Claude is the
decider. It is told what each action does and what the made-up dollars are, and it asks for the case's facts
with lookups: the forecast, the trucks and the waiting calls. A lookup returns counts, days and odds and never
a price, so Claude does the arithmetic itself. It ends by committing one action, a reason in one sentence and
a note a person could send. Nothing here sends anything.

The answers ship saved in data/, one file for each model, named by the prompt that made them, so a run needs no
network or key. `python -m staple.agent MODEL --cap DOLLARS` asks the Claude API, with the reader's own
ANTHROPIC_API_KEY, for the cases the file lacks. It stops before the dollars spent could pass the cap, and what
was answered until then stays saved.
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from staple import triage
from staple.buyer import ACTIONS, EXPEDITE, EXPEDITE_DAYS, REBOOK, RUSH, SEND, SHIFT, WAIT_DAY
from staple.load import MOVE, REPEAT_VISIT

MODELS = ("claude-haiku-5-5", "claude-opus-5-5")
PRICED_ON = "2026-11-11"    # the day the prices below were read from the model list. Check them before a run.
# Dollars for a million tokens in and out, as each model was listed that day.
# These are Haiku's prices for a prompt of up to 100,000 tokens, and a case here sends a few thousand.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}
CACHE_WRITE = 1.25     # a cached token's price to write, kept five minutes, as a share of the input price
# A cached token's price to read, as a share of the input price. It differs by model.
CACHE_READ = {"claude-haiku-5-5": 0.10, "claude-sonnet-5-5": 0.05, "claude-opus-5-5": 0.05}
EFFORT = "medium"
MAX_TOKENS = 8000      # the ceiling on one reply, its thinking included
MAX_ROUNDS = 6         # the requests one case may take before it is left undecided
PLACES = 6             # decimal places a lookup gives for a count of calls or an odds
WORKERS = 4            # cases asked at once
SAVE_EVERY = 25        # cases between saves, so a stopped run keeps what it paid for
BUSY = (429, 529)      # the API's answers for a rate limit reached and for an overload
WAITS = (2, 5, 15, 45)  # seconds to wait before asking again after one of those
ROUND_LIMIT, CUT_OFF, DECLINED = "round limit", "reply cut off", "declined"
DATA = Path(__file__).resolve().parent.parent / "data"
MARKUP = re.compile(r"</?[A-Za-z][^<>]*>")    # a tag, such as a tool call's own syntax leaves behind in a reply's words
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")
PART_FACT, DAY_FACT, TRUCKS_FACT = "the part", "the ship day", "both trucks"

FORECAST, TRUCKS, WAITING, COMMIT = "back_order_forecast", "trucks_holding_part", "calls_waiting_on_part", "commit_decision"
FIELDS = {FORECAST: ("part",), TRUCKS: ("part",), WAITING: ("part",), COMMIT: ("action", "reason", "note")}

SYSTEM = f"""You are the parts buyer for a fleet of appliance repair trucks. One part is back-ordered: no center can ship it until its ship day. Until then a call that needs the part, with no truck nearby holding one, fails, and each failed call costs a repeat visit of ${REPEAT_VISIT:.0f}.

Choose one of four actions for the part today:
- {SEND}: change nothing. Every call expected to be stuck risks a repeat visit.
- {RUSH}: pay ${EXPEDITE:.0f} and the part ships in {EXPEDITE_DAYS} days, or on its own ship day when that is sooner. Calls come evenly over the days until the part ships, so only the share of the stuck calls that falls before the earlier ship day still risks a repeat visit.
- {SHIFT}: take one part off a truck that holds it and put it on a truck that holds none. That is two moves at ${MOVE:.0f} each. A lookup names the one move on offer and the calls still expected to be stuck after it. This action cannot be chosen when no move is on offer.
- {REBOOK}: move every booked call that is waiting on the part to the ship day. Each of them waits the days until the part ships, at ${WAIT_DAY:.0f} a call a day, and no longer risks a repeat visit for this part. Take each waiting call's odds of needing the part off the calls expected to be stuck, never going below zero. This action cannot be chosen when no call is waiting.

Work out the expected dollars of each action that can be chosen and pick the cheapest. On a tie, pick the one listed first.

Get the facts with the lookups, asking for all you need at once. Then call {COMMIT} once with the action, a reason in one sentence, and a short note a person could send to whoever must act: the technicians for {SEND}, the supplier for {RUSH}, the parts room for {SHIFT}, the customers for {REBOOK}. The note names the part and its ship day, and for a move both trucks."""
OPENING = "Today is {day}. The part {part} is back-ordered. Decide what to do about it today."
NUDGE = f"Call {COMMIT} with your action, reason and note."


def tool(name, description, fields):
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}}


PART = {"part": {"type": "string", "description": "The back-ordered part, named as the case names it"}}
TOOLS = [
    tool(FORECAST, "The forecast for the back-ordered part: its ship day, the days until then, the calls expected to "
         "need it before it ships, and how many of those are expected to be stuck with no truck nearby holding one. "
         "Call this first.", PART),
    tool(TRUCKS, "The trucks that hold the back-ordered part, and the one move on offer: which truck would give a part, "
         "which would take it, and the calls still expected to be stuck after it. Call this before choosing to move one.",
         PART),
    tool(WAITING, "The booked calls already waiting on the back-ordered part, each with its odds of needing it. Call "
         "this before choosing to rebook.", PART),
    tool(COMMIT, "Commit the decision for this part. Call this once, after the lookups, to end the case.",
         {"action": {"type": "string", "enum": list(ACTIONS), "description": "The action chosen"},
          "reason": {"type": "string", "description": "Why, in one sentence"},
          "note": {"type": "string", "description": "A short note a person could send to whoever must act"}}),
]
PROMPT_ID = hashlib.sha256(json.dumps([EFFORT, MAX_TOKENS, MAX_ROUNDS, PLACES, SYSTEM, OPENING, NUDGE, TOOLS],
                                      sort_keys=True).encode()).hexdigest()[:triage.ID_LENGTH]


class BadCall(Exception):
    """A tool call that cannot be answered. Its message goes back to Claude as the tool's error."""


class CapReached(Exception):
    """The run's spending cap leaves no room for another request."""


def name_of(case):
    return f"{case['day']} {case['part']}"


def ships_on(case):
    """The day a center can ship the case's part."""
    return dt.date.fromisoformat(case["day"]) + dt.timedelta(days=case["days_left"])


def facts(case, lookup):
    """What one lookup returns for a case: counts, days and odds, and never a price."""
    if lookup == FORECAST:
        return {"part": case["part"], "today": case["day"], "ships_on": str(ships_on(case)), "days_until_it_ships": case["days_left"],
                "calls_expected_to_need_it": round(case["expected"], PLACES),
                "calls_expected_to_be_stuck": round(case["stuck"], PLACES)}
    if lookup == TRUCKS:
        move = case["move"] and {"from_truck": case["move"]["giver"], "to_truck": case["move"]["taker"],
                                 "calls_expected_to_be_stuck_after": round(case["move"]["stuck"], PLACES)}
        return {"trucks_holding_it": case["holders"], "zones_with_no_holder_nearby": case["open_zones"], "move_on_offer": move}
    return {"calls_waiting": [{"call": job, "odds_it_needs_the_part": round(odds, PLACES)} for job, odds in case["waiting"]]}


def answer(case, call):
    """One tool call answered: a lookup's facts, or the decision a commit carries. A call outside the agreed
    form raises BadCall."""
    name, given = call.get("name"), call.get("input")
    if name not in FIELDS:
        raise BadCall(f"there is no tool named {name}; the tools are {', '.join(FIELDS)}")
    fields = FIELDS[name]
    if not (isinstance(given, dict) and set(given) == set(fields)
            and all(isinstance(given[f], str) and given[f].strip() for f in fields)):
        raise BadCall(f"{name} takes exactly these fields, each as text: {', '.join(fields)}")
    if name == COMMIT:
        if given["action"] not in ACTIONS:
            raise BadCall(f"the action must be one of: {', '.join(ACTIONS)}")
        return {f: given[f].strip() for f in fields}
    if given["part"] != case["part"]:
        raise BadCall(f"this case is about {case['part']}; ask about that part")
    return facts(case, name)


def decide(case, ask, model):
    """One case put to Claude: its lookups answered until it commits an action, or the round limit comes. ask
    sends one request and returns the reply. A case left undecided names no action and says what stopped it."""
    messages = [{"role": "user", "content": OPENING.format(day=case["day"], part=case["part"])}]
    out = {"action": None, "reason": "", "note": "", "rounds": 0, "lookups": [], "usage": Counter()}
    for _ in range(MAX_ROUNDS):
        reply = ask({"model": model, "max_tokens": MAX_TOKENS, "output_config": {"effort": EFFORT}, "tools": TOOLS,
                     "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                     "messages": messages})
        out["rounds"] += 1
        out["usage"].update({k: v for k, v in (reply.get("usage") or {}).items() if type(v) is int})
        stopped = {"refusal": DECLINED, "max_tokens": CUT_OFF}.get(reply.get("stop_reason"))
        if stopped:        # a reply that ended this way may hold half a tool call, so none of its calls is run
            return {**out, "stopped": stopped}
        content = reply.get("content") or []
        calls = [block for block in content if block.get("type") == "tool_use"]
        # The whole reply goes back as it came: its thinking is tied to this conversation and its calls need answers.
        messages.append({"role": "assistant", "content": content})
        if not calls:
            messages.append({"role": "user", "content": NUDGE})
            continue
        results = []
        for call in calls:
            try:
                found = answer(case, call)
            except BadCall as err:
                results.append({"type": "tool_result", "tool_use_id": call.get("id"), "is_error": True, "content": str(err)})
                continue
            if call["name"] == COMMIT:
                return {**out, **found}
            out["lookups"].append(call["name"])
            results.append({"type": "tool_result", "tool_use_id": call.get("id"), "content": json.dumps(found)})
        messages.append({"role": "user", "content": results})
    return {**out, "stopped": ROUND_LIMIT}


def dollars(model, usage):
    """What the API's own token counts cost at the listed prices."""
    into, out = PRICES[model]
    return (usage.get("input_tokens", 0) * into + usage.get("cache_creation_input_tokens", 0) * into * CACHE_WRITE
            + usage.get("cache_read_input_tokens", 0) * into * CACHE_READ[model] + usage.get("output_tokens", 0) * out) / 1e6


def ceiling(model, body):
    """The most one request can cost. A token is at least one character of the request as it is sent, so every
    character is priced as a token written to the cache, and the reply as a full one."""
    into, out = PRICES[model]
    return (len(json.dumps(body)) * into * CACHE_WRITE + body["max_tokens"] * out) / 1e6


class Budget:
    """A run's spending cap. A request goes out only while the dollars spent, plus the most every request still
    out could cost, stay within the cap, so the cap is never passed."""

    def __init__(self, model, cap, spent=0.0):
        self.model, self.cap, self.spent, self.held = model, cap, spent, 0.0
        self.lock = threading.Lock()

    def ask(self, body, send):
        most = ceiling(self.model, body)
        with self.lock:
            if self.spent + self.held + most > self.cap:
                raise CapReached(f"the ${self.cap:,.2f} cap leaves no room for another request, with "
                                 f"${self.spent:,.2f} spent")
            self.held += most
        try:
            reply = send(body)
        except BaseException:
            with self.lock:
                self.held -= most
            raise
        usage = reply.get("usage") or {}
        with self.lock:
            self.held -= most
            # A reply that does not say what it used is charged the most it could have cost.
            self.spent += dollars(self.model, usage) if type(usage.get("output_tokens")) is int else most
        return reply


def patient(send, waits=WAITS, sleep=time.sleep):
    """send, asked again after a wait while the API says it is busy."""
    def again(body):
        for wait in waits:
            try:
                return send(body)
            except triage.LiveError as err:
                if err.status not in BUSY:
                    raise
                sleep(wait)
        return send(body)
    return again


def path(model):
    return DATA / f"buyer-{model}.json"


def kept(model):
    """The saved file for a model as it stands, or None when there is none."""
    try:
        return json.loads(path(model).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def saved(model):
    """The saved answers for a model, or None when none were made with this prompt."""
    file = kept(model)
    return file["answers"] if file and file["prompt_id"] == PROMPT_ID else None


def save(model, answers, spent):
    """The answers, and every dollar spent asking for them, a case cut short included."""
    DATA.mkdir(exist_ok=True)
    path(model).write_text(json.dumps({"model": model, "effort": EFFORT, "prompt_id": PROMPT_ID, "spent": spent,
                                       "answers": answers}, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def chooser(answers):
    """Saved answers as a decider for the scorer. A case with no decision names no action, which the scorer
    counts as one that could not be carried out."""
    return lambda case: answers.get(name_of(case), {}).get("action")


def cost(model, answers):
    """The dollars a model's saved decisions cost, from the token counts saved with each."""
    return dollars(model, sum((Counter(a["usage"]) for a in answers.values()), Counter()))


def as_written(text):
    """A reason or a note up to the first stray markup, and whether any was cut off there."""
    found = MARKUP.search(text)
    return (text[:found.start()].rstrip(), True) if found else (text, False)


def names_day(text, day):
    """Whether a text names a day, written as 2026-08-03, August 3 or Aug 3."""
    month = MONTH_NAMES[day.month - 1]
    return bool(re.search(rf"\b(?:{day.isoformat()}|{month} {day.day}|{month[:3]}\.? {day.day})\b", text))


def left_out(case, answer):
    """The facts the prompt asks of a note that a decision's note leaves out: the part, its ship day, and for a
    move both trucks. A part named in the singular counts, so one gasket names the gaskets."""
    note, own = answer["note"], case["part"].split("/")[1].lower()
    out = [] if (own[:-1] if own.endswith("s") else own) in note.lower() else [PART_FACT]
    if not names_day(note, ships_on(case)):
        out.append(DAY_FACT)
    move = answer["action"] == SHIFT and case["move"]
    if move and not all(re.search(rf"\b{re.escape(t)}\b", note) for t in (move["giver"], move["taker"])):
        out.append(TRUCKS_FACT)
    return out


def words(cases, answers):
    """How many of a model's decisions carry words a person could not send as they are: a note that leaves out a
    fact the prompt asks for, and a reason or note that runs on into stray markup."""
    made = [(c, answers[name_of(c)]) for c in cases if answers.get(name_of(c), {}).get("action")]
    return {"notes_short": sum(bool(left_out(c, a)) for c, a in made),
            "ran_on": sum(any(as_written(a[f])[1] for f in ("reason", "note")) for _, a in made)}


def scored(cases):
    """Each model whose saved decisions cover every case, as (its name, the model, the decisions), and one
    sentence for each saved file that cannot be scored as it stands."""
    found, left = [], []
    for model in MODELS:
        file = kept(model)
        if file is None:
            continue
        have = sum(name_of(c) in file["answers"] for c in cases)
        if file["prompt_id"] != PROMPT_ID:
            left.append(f"{path(model).name} was made with a different prompt, so it is not scored")
        elif have < len(cases):
            left.append(f"{path(model).name} holds {have:,} of {len(cases):,} decisions, so it is not scored until "
                        f"`python -m staple.agent {model} --cap DOLLARS` asks for the rest")
        else:
            found.append((triage.named(model), model, file["answers"]))
    return found, left


def leading(conn):
    """The scored model whose decisions lost the fewest dollars, as (its name, its decisions), or None when no
    model is scored. A tie goes to the model listed first."""
    names = [triage.named(m) for m in MODELS]
    lost = {d: v for d, v in conn.execute("SELECT decider, lost FROM buyer_score") if d in names}
    if not lost:
        return None
    best = min(lost, key=lambda d: (lost[d], names.index(d)))
    return best, saved(MODELS[names.index(best)])


def run(found, model, key, cap, url=triage.API_URL, fresh=False):
    """Claude's decision on every case the saved file lacks, saved as it goes. The run stops with one plain
    sentence when the API fails or the cap is reached, and the decisions made until then stay saved."""
    file = kept(model)
    if file and file["prompt_id"] != PROMPT_ID and not fresh:
        raise SystemExit(f"{path(model).name} was made with a different prompt and cost money: move it aside, or "
                         "pass --fresh to replace it")
    file = file if file and file["prompt_id"] == PROMPT_ID else {"answers": {}, "spent": 0.0}
    answers = dict(file["answers"])
    budget = Budget(model, cap, file["spent"])     # the cap covers every run that filled this file
    send = patient(lambda body: triage.post(body, key, url))
    halted = []

    def one(case):
        if not halted:
            try:
                answers[name_of(case)] = decide(case, lambda body: budget.ask(body, send), model)
            except (triage.LiveError, CapReached) as err:
                halted.append(err)

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            try:
                for done, _ in enumerate(pool.map(one, [c for c in found if name_of(c) not in answers]), 1):
                    if done % SAVE_EVERY == 0:
                        save(model, dict(answers), budget.spent)
            except BaseException as err:     # a stop from the keyboard: the cases still out finish and no more start
                halted.append(err)
                raise
    finally:
        save(model, dict(answers), budget.spent)
    if halted:
        have = sum(name_of(c) in answers for c in found)
        raise SystemExit(f"the buyer run stopped: {halted[0]}. {have:,} of {len(found):,} decisions are saved; "
                         "run it again to go on")
    return answers


if __name__ == "__main__":
    from staple import buyer, db, world
    args = sys.argv[1:]
    fresh = "--fresh" in args
    args = [a for a in args if a != "--fresh"]
    try:
        model, flag, cap = args
        cap = float(cap)
        if model not in PRICES or flag != "--cap" or cap <= 0:
            raise ValueError
    except ValueError:
        raise SystemExit(f"usage: python -m staple.agent MODEL --cap DOLLARS [--fresh], where MODEL is one of "
                         f"{', '.join(PRICES)}") from None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("the buyer run needs ANTHROPIC_API_KEY set to your own Claude API key")
    made_up = db.connect(":memory:")     # the cases come from the world itself, so no earlier run is needed
    world.build(made_up)
    cases = buyer.cases(made_up)
    made = run(cases, model, key, cap, fresh=fresh)
    undecided = sum(a["action"] is None for a in made.values())
    print(f"{len(made):,} decisions from {triage.named(model)} saved in {path(model)}; {undecided or 'none'} left undecided; "
          f"${kept(model)['spent']:,.2f} spent in all at the listed prices")
