# Walkthrough

How Staple fits together, file by file, in the order the one command runs it. Every figure Staple produces
comes from invented history, so this page names no result. The results live on the
[live report](https://acep2317.github.io/staple/), built from the files below.

## The flow

`python run.py` works like a planning run. It first writes the master data and three years of history into
one SQLite file. Each step after that reads the file and writes its answer back as a table: the parts
predictor's back-test, the truck loads, the back-order list, the AI step and the dispatch replay. The metric
queries read only those tables, and the pages read only the query results. So a number on the report can
always be traced to one query, and that query to one table.

## The one command and its checks

- `run.py` is the one command. It builds the world, runs every step in order, prints a summary of each, runs
  the metric queries and writes the three pages into `out/`. With `--pages` it also writes the copy GitHub
  Pages serves, and with `--live` it asks Claude afresh instead of reading the cached answers.
- `check.py` holds every check, each runnable alone by name (`python check.py report`). Each one plants a
  fault or a known answer and must catch it, because a check that never fails looks exactly like one that
  sees nothing. It guards the size ceiling, the ban on third-party packages and a private list of names
  that must never appear in a public file. It recomputes the predictor by hand and holds each claimed gain
  against its control. It also checks the load and dispatch rules, the AI step's calibration, the metric
  queries, the pages, this walkthrough and a fresh public copy of the repository.

## The made-up world

- `staple/world.py` invents everything: appliance types, model families, problems, parts with a cost and a
  size, technicians with a skill and speed per appliance, trucks, zones and distribution centres. It also
  writes three years of repair jobs. It plants noise on purpose, so the methods are tested against it:
  customers who describe the wrong problem, thin history on the newest families, and failures that follow
  no pattern. One fixed seed writes the same rows every time.
- `staple/db.py` opens the one SQLite file, `out/staple.db`, and starts it empty on each run.
- `sql/schema.sql` defines every table: the master data first, then the jobs and the parts each one needed,
  then one table per step's results. `staple/world.py` applies it before writing a row.
- `staple/__init__.py` marks the folder as one Python package and says in a line what the package is for.

## Predicting the parts, and scoring the prediction

- `staple/predict.py` ranks the parts most likely to fix a booked repair. It counts what fixed past jobs at
  three levels: the same model family and problem, the appliance type and problem, and the appliance type
  alone. A level with little history leans on the level above it. In planning terms it is a where-used list
  of past fixes, with a safety net for a model too new to have history of its own.
- `staple/backtest.py` scores the predictor the way a forecast is held to actuals. It learns from all but the
  last three months, then predicts those months as if they had not happened yet. A job counts as covered
  only when every part it needed was on the list.

## Trucks, back-orders and dispatch

- `staple/load.py` builds each truck's load for tomorrow. It replays every held-back truck-day under several
  ways of loading: today's fixed stock, that stock topped up from the carry lists, the lists alone, everything
  that fits, and a balanced truck. The balanced truck keeps a part only while its expected saving outweighs
  the cost of moving and holding it. That is the same netting a planner does, demand against stock with a
  carrying cost. The module also marks where each carry-list part would come from, and flags a booking
  likely to need a part nobody nearby holds.
- `staple/backorders.py` ranks every part the centres cannot ship by the second visits it puts at risk before
  it arrives: an expedite list ordered by exposure. Which booking will need a stuck part cannot be known in
  advance, but how many will can, so the action is fleet-wide. The list is held to what the held-back days
  then brought.
- `staple/dispatch.py` replays every held-back day under each way of handing out calls, with the same
  invented sick calls, breakdowns and priority changes. Staple's rule sends each call to the technician
  with the lowest expected cost. Within each priority, the call with most to lose if its best technician
  fills up is placed first. A call no route has room for rolls to tomorrow and counts as not fixed.

## The AI step

- `staple/triage.py` has Claude read the customer's own words and pick the problem code, with a confidence.
  When it is unsure, the reading leans on the appliance's overall odds, and Claude writes one short question
  for the customer. Claude sees only the appliance type and each code's label, never the invented phrasings.
  Its score is held against keyword rules and two controls: its answers shuffled between bookings, and
  random replies to its question.
- `data/triage-cache.json` holds Claude's answers for every customer phrasing, made once with Claude Haiku
  5.5, so a run needs no network and no key.
- `data/followup-cache.json` holds the follow-up question Claude wrote for each unsure phrasing, with the
  answer choices and the problem code each one points to.

## The metrics

- `staple/metrics.py` writes the made-up costs and rates from the code into an `assumptions` table, runs
  every query in `sql/metrics/`, and hands the results to the pages. It also saves each result as a CSV in
  `out/metrics/`, so a reader can open any table without running SQL.
- `sql/metrics/first_time_fix.sql`: the share of jobs fixed on the first visit, by month.
- `sql/metrics/parts_caused_reschedules.sql`: repeat visits caused by a missing part, by appliance.
- `sql/metrics/truck_fill_rate.sql`: of the part lines jobs needed, the share already on the truck, and the
  share of jobs with every line there.
- `sql/metrics/slow_truck_stock.sql`: what each truck holds that its own technician's jobs did not need in
  the year before the held-back months.
- `sql/metrics/inventory_roi.sql`: each way of loading a truck, with its coverage, stock, overnight moves,
  costs a truck-day and the repeat-visit dollars a year its stock avoids per dollar held.
- `sql/metrics/carry_list_curve.sql`: coverage against carry-list length, for the predictor, the national
  top-parts list and the shuffled-history control.
- `sql/metrics/backorder_exposure.sql`: each back-ordered part, its forecast calls and stuck calls before it
  ships, and what the held-back days then brought.
- `sql/metrics/ai_step.sql`: each reading of the customer's words, its agreement with the booked code and the
  true problem, and the coverage of the list it leads to.
- `sql/metrics/dispatch_scorecard.sql`: each way of handing out calls, with fixes, drive time, rolled calls,
  moved calls and cost a call.

## The pages

- `staple/report.py` builds the three pages from the query results and the code's own constants. It writes a
  hover hint for nearly every number, heading, bar and column, and stamps each page with the build that made
  it. The manuals quote every constant, prompt, query and check straight from the code, so they cannot
  disagree with what ran.
- `staple/pages/house.css` is the look: colours as named tokens, light and dark, the hover marker and the
  phone layout.
- `staple/pages/tips.js` shows a hint on hover, keyboard focus or tap, and moves the chart's crosshair.
- `staple/pages/manual.html` is the instruction manual's text, with a named gap wherever a value from the
  code goes.
- `staple/pages/method.html` is the methods manual's text: every formula, rule, prompt and check, filled the
  same way.
- `docs/index.html`, `docs/manual.html` and `docs/method.html` are the published copy GitHub Pages serves.
  `python run.py --pages` writes them, and `check.py report` refuses a copy that differs from a fresh build.

## Repository plumbing

- `README.md` is the front page: what Staple does, how it is kept honest, how to run it, and what a real
  system must also handle.
- `WALKTHROUGH.md` is this page.
- `.gitignore` keeps the built output, Python's caches and a private folder out of the repository.
- `.gitattributes` keeps shell scripts' line endings Unix-style, because bash fails on a Windows line ending.
- `scripts/guard.sh` runs the repository's own guards before each commit: one test per mistake already made.
- `scripts/guards/fast/public-files-carry-no-private-names.sh` runs the size, package and private-names
  check on every commit, so nothing reaches the public repository without it.
- `scripts/guards/fast/a-guards-throwaway-repo-writes-into-the-commit-being-made.sh` makes sure a guard that
  builds a scratch repository can never write into the commit being made.
