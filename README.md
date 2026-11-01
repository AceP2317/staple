# Staple

Which parts belong on the truck, and which technician should take the call.

**[Open the live report](https://acep2317.github.io/staple/)** · [Instruction manual](https://acep2317.github.io/staple/manual.html) · [Methods and logic](https://acep2317.github.io/staple/method.html)

Every figure in this repository comes from repair history I invented. The method is real and the results
are not. On real history, the same back-test is the first thing I would run.

## The problem

A technician who arrives without the right part cannot finish the repair, and the customer waits for a
second visit. Two decisions set that up the night before: what each truck carries, and which technician
takes each call. When the stock lives on the trucks, those are one decision, so Staple scores them together.

## What it does

- **Predicts the parts.** For a booked repair, it ranks the parts most likely to fix it by counting what
  fixed past jobs on the same model family and problem. Where a family's history is thin, it leans on the
  wider appliance group.
- **Loads the truck.** Each night it builds every truck's load from tomorrow's booked calls. A part stays
  aboard only while the second visits it saves outweigh the space and money it ties up.
- **Marks where each part comes from.** On this truck, from a distribution centre overnight, on another
  truck nearby, back-ordered, or locked to a certified technician. A booking likely to need a part nobody
  nearby holds gets flagged before the visit.
- **Ranks the back-orders.** Each part the centres cannot ship, by the second visits it puts at risk before
  it arrives.
- **Reads the customer's words.** Claude Haiku 5.5 (`claude-haiku-5-5`) turns what the customer said into a
  problem code with a confidence, and asks one short follow-up question when it is unsure. Its answers ship
  cached in the repository, so a run needs no key.
- **Sends the call.** It replays every held-back day with sick calls, breakdowns and priority changes. Each
  call goes to the technician with the lowest expected cost: drive and repair time, plus a second visit
  times the chance that technician's skill or truck fails the job.
- **Measures it in SQL.** First-time fix, parts-caused reschedules, truck fill rate, slow truck stock,
  inventory ROI, back-order exposure and the dispatch scorecard are plain queries in
  [`sql/metrics/`](sql/metrics/). Every number on the report comes from one of them.

## How it is kept honest

- **A held-back period.** The history holds 160,000 jobs over three years. Every method learns from the
  days before the last 92 days and is scored on those 92 as if they had not happened yet.
- **A control for every gain.** Each claimed gain has a deliberately broken twin: shuffled history,
  shuffled technician skills, shuffled AI answers, random replies to the follow-up question. A gain counts
  only when its control falls back.
- **Checks that can fail.** `python check.py` runs every check, and each one plants a fault or a known
  answer it must catch. A check that never fails looks exactly like one that sees nothing.
- **One seed.** Seed 2026 rebuilds every row, so every figure reproduces exactly.

## Run it

Python 3 is the only requirement, and nothing is installed. It is built and tested on Python 3.14.

```
python run.py            # the made-up world, every result, the metric tables and the three pages, into out/
python check.py          # every check, each with a control that proves it can fail
python run.py --live     # the AI step asked afresh, with your own ANTHROPIC_API_KEY
python run.py --pages    # also writes the published copy of the pages to docs/
```

Then open `out/index.html` in a browser.

## On real history

I would run the same back-test first: learn from all but the last three months, score those three, and
keep only the gains whose controls fall back. Then a one-region pilot against the current way of loading
trucks, measured on first-time fix and parts-caused reschedules.

## What a real system must also handle

- **Quantities.** Staple treats a truck that holds a part as holding enough of it.
- **Roads.** Drive time here is zone to zone on a grid. A real run hands the dispatch score to the routing
  system a dispatch team already owns.
- **The whole day at once.** The night plan places calls one at a time; a solver plans the day together.
- **Real costs.** The repeat-visit, handling, holding and minute costs are stand-ins, each stated once in
  the code and listed on the instruction manual's threshold table.
- **Real customers.** The customer phrasings are a small invented set.
- **Failures with the parts aboard.** The history records each technician's success rate, not wrong
  diagnoses or no-shows.
- **Live feeds.** Staple reads one SQLite file. A real one reads the dispatch and inventory feeds as they
  change.

## How it was built

Designed and directed by Ian Provencher, built with Claude Code. Python's standard library and SQLite only,
held under a size ceiling that `check.py` enforces. [WALKTHROUGH.md](WALKTHROUGH.md) explains every file in
plain words.
