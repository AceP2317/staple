# Walkthrough

Two parts. The first is a path through the live report for presenting it to a room in twenty minutes. The
second is how Staple fits together, file by file, in the order the one command runs it. Every figure Staple
produces comes from invented history, so this page names no result. The results live on the
[live report](https://acep2317.github.io/staple/), built from the files below.

## Presenting the report in twenty minutes

Open the [live report](https://acep2317.github.io/staple/) and stay on that one page. The steps run down it in
order. Each names what to click and the one thing to say.

With ninety seconds and not twenty minutes, press Overview at the top of the report. That one page holds the
purpose, the key points and the functions, and it is short enough to say aloud in that time.

- **The first screen, three minutes.** Read the sentence under the name aloud, then point at the invented-data
  label: the method is real and the numbers are not. The scorecard is the whole plan against the current rules
  over every held-back day. Read one row across: the current rules, Staple, the difference, then the control.
  Say what the control is before anyone asks. It is Staple with the history behind its part odds shuffled, so it
  shows how much of each gain needs nothing finer than what each appliance usually needs.
- **The to-do list, two minutes.** Beside the scorecard is what people do before the day starts, grouped by who
  does it: the parts room, dispatch and the buyer. On a day when a customer's answer left a booking unsure, the
  call center has a group too. Click one parts-room item. The page moves to the plan, lights up that truck and
  opens its load sheet.
- **The plan at night, three minutes.** Each row of the board is a technician and each block a call, shaded by
  its chance of a first-time fix. Click a call. It opens beside the board in the customer's own words, with
  Claude's reading, the parts and their odds, and why that technician took it. Then read one row of the open
  load sheet: put on or take off, the part, its worth, and the calls behind it.
- **The day as it ran, three minutes.** Switch to As the day ran. The hatched row is a technician who called
  out sick, a warning mark is a truck that broke down, and every ringed call moved to another truck. Open one
  moved call and read its first-time fix chance on the planned truck and on the new one. A moved call is scored
  on the truck that took it, with that technician's skill and only the parts already aboard, which is why the
  chance can fall.
- **AI at booking, two minutes.** Under the plan, each card is a booking Claude was unsure of: the customer's
  words, its first reading, the one question it asked and the answer taken. Where the answer changed the parts
  that call loads, the card shows the first guess's parts and the new ones. Say plainly that the customer who
  answers is simulated, so that gain is an upper bound.
- **One piece of evidence and its control, three minutes.** Scroll to the evidence and stop at its first card,
  Who takes the call. Its bars set each way of handing out calls side by side, with the controls among them.
  Then go to the last card, Each gain against its control, which names the controls that do not fall back and
  says what that means.
- **What it does not show, two minutes.** Read the panel What this shows, and what it does not: the history is
  invented, so nothing here shows a gain on a real fleet, and some corners are cut on purpose. The panel beside
  it gives the order a fleet would adopt it in, and its first step is the same back-test on real history.
- **How to run it, two minutes.** The last panel names the two commands that rebuild and check everything, and
  the instruction manual walks through a first run.

## The flow

`python run.py` works like a planning run. It first writes the master data and three years of history into
one SQLite file. Each step after that reads the file and writes its answer back as a table: the parts
predictor's back-test, the truck loads, the back-order list, the AI step and the dispatch replay. The metric
queries read only those tables, and the report's evidence reads only the query results. So a number in the
evidence can always be traced to one query, and that query to one table. The day's plan on the report is the
dispatch replay's own record of the most recent week, so every route and load on it is one the replay decided.

## The one command and its checks

- `run.py` is the one command. It builds the world, runs every step in order, prints a summary of each, runs
  the metric queries, records the week's plan and writes the five pages into `out/`. With `--pages` it also writes the copy GitHub
  Pages serves, and with `--live` it asks Claude afresh instead of reading the cached answers. With `--card` or
  `--picture` it only takes a photograph, as the two picture files below say.
- `check.py` holds every check, each runnable alone by name (`python check.py report`). Each one plants a
  fault or a known answer and must catch it, because a check that never fails looks exactly like one that
  sees nothing. It guards the ban on third-party packages, a private list of names that must never appear
  in a public file, and the weight of each published page. It recomputes the predictor by hand and holds each claimed gain
  against its control. It also checks the load and dispatch rules, the AI step's calibration, the week's plan,
  the pages and this walkthrough. Every column of every metric query is recomputed in Python, and each query
  must also lose to one wrong version of itself planted by the check. Two more checks run only when named,
  because each runs the whole suite in another folder. `python check.py first-run` is a stranger's first run:
  an empty folder with no home folder, no key and no web. `python check.py fresh-clone` holds a fresh public
  copy of the repository to the live pages.

## The made-up world

- `staple/world.py` invents everything: appliance types, model families, problems, parts with a cost and a
  size, technicians with a skill and speed per appliance, trucks, zones and distribution centers. It also
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
- `staple/backorders.py` ranks every part the centers cannot ship by the repeat visits it puts at risk before
  it ships: an expedite list ordered by exposure. Which booking will need a stuck part cannot be known in
  advance, but how many will can, so the action is fleet-wide. A back-order starts on one day and ships on
  another, so the list is made again for every held-back day and a part drops off the day it ships. Each
  forecast is held to what the held-back days then brought.
- `staple/buyer.py` prices the buyer's four actions on each of those stuck parts, in expected dollars: send the
  calls anyway, expedite the part, move one from a truck unlikely to need it, or rebook the calls waiting on
  it. Anything that names an action for a case can be scored by it, as the dollars its choices cost over the
  cheapest. Two fixed rules and a control that reads another case's facts are scored beside the arithmetic,
  and so is each Claude model whose saved decisions cover every case.
- `staple/dispatch.py` replays every held-back day under each way of handing out calls, with the same
  invented sick calls, breakdowns and priority changes. Staple's rule sends each call to the technician
  with the lowest expected cost. It learns each technician's success rate from the training history, and the
  rates the invented world was written from only score the result. Within each priority, the call with most
  to lose if its best technician fills up is placed first. The day then runs in clock order: no technician
  sets out for a call before the minute it came in or was moved to them. A call no route has room for rolls
  to tomorrow and counts as not fixed.

## The AI step

- `staple/triage.py` has Claude read the customer's own words and pick the problem code, with a confidence.
  When it is unsure, the reading leans on the appliance's overall odds, and Claude writes one short question
  for the customer. Claude sees only the appliance type and each code's label, never the invented phrasings.
  Its score is held against a plain keyword matcher and two controls: its answers shuffled between bookings,
  and random replies to its question. The customer who answers the question is simulated and always picks the
  matching answer, so that score is labeled an upper bound.
- `data/triage-cache.json` holds Claude's answers for every customer phrasing, made once with Claude Haiku
  5.5, so a run needs no network and no key.
- `data/followup-cache.json` holds the follow-up question Claude wrote for each unsure phrasing, with the
  answer choices and the problem code each one points to.
- `staple/agent.py` puts Claude in the buyer's seat. It holds one conversation for each back-ordered part.
  Claude asks for the forecast, the trucks and the waiting calls through lookups that return counts, days and
  odds and never a price, then commits one action, a reason and a note a person could send. The scorer in
  `staple/buyer.py` prices what it chose. A run asks the Claude API with the reader's own key, saves each
  decision as it comes, and stops before the dollars spent could pass the cap it was given. It also reads
  each saved decision's words: whether the note names the part, its ship day and, for a move, both trucks,
  and whether the reply runs on into stray markup.
- `data/buyer-claude-haiku-5-5.json` and `data/buyer-claude-opus-5-5.json` hold each model's decision on every
  back-ordered part of every held-back day: the action, the reason, the note, the lookups it made and the
  token counts the API returned. Each file is named by the prompt that made it, and is scored only while it
  matches that prompt and covers every case.

## The week's plan

- `staple/plan.py` turns the most recent week of held-back days into the plan the report shows. It recodes
  each booking with Claude's reading of the customer's words, asking the saved follow-up question where
  Claude was unsure. It replays the week under Staple's rule and under the current rules, seeded so the sick
  calls and breakdowns are the ones the full replay met on the same days, and records every route, load and
  move as the replay makes it. It then shapes each day into what people act on: a load sheet per truck, the
  calls to warn or rebook, the back-orders to chase, and every call a sick call or breakdown moved. Nothing
  in it decides anything. Like an MRP run that keeps its planned orders instead of printing only the service
  level, it keeps what the replay already planned.

## The metrics

- `staple/metrics.py` writes the made-up costs and rates from the code into an `assumptions` table, runs
  every query in `sql/metrics/`, and hands the results to the pages. It also saves each result as a CSV in
  `out/metrics/`, so a reader can open any table without running SQL.
- `sql/metrics/first_time_fix.sql`: the share of jobs fixed on the first visit, by month.
- `sql/metrics/parts_caused_reschedules.sql`: repeat visits caused by a missing part, by appliance.
- `sql/metrics/parts_cycle_time.sql`: the days a repair waits for its parts, as the history recorded them and as
  each way of handing out calls would leave them, with the share of waits that are on a back-ordered part.
- `sql/metrics/truck_fill_rate.sql`: of the part lines jobs needed, the share already on the truck, and the
  share of jobs with every line there.
- `sql/metrics/scorecard.sql`: the scorecard's rows. Each is one measure of the whole plan under the current
  rules, under Staple and under Staple on shuffled history, with the difference and the share of it that control
  keeps.
- `sql/metrics/slow_truck_stock.sql`: what each truck holds that its own technician's jobs did not need in
  the year before the held-back months.
- `sql/metrics/inventory_roi.sql`: each way of loading a truck, cheapest first, with its coverage, stock,
  overnight moves, costs a truck-day and the repeat-visit dollars a year its stock avoids per dollar held.
  The shuffled-history controls and the balanced truck's tests are rows here too: held to the space today's
  stock uses, and with a higher price for one move.
- `sql/metrics/carry_list_curve.sql`: coverage against carry-list length, for the predictor, the national
  top-parts list and the shuffled-history control. One more column scores the predictor with the held-back
  jobs leaked into its history, which shows what holding them back is worth.
- `sql/metrics/controls.sql`: every control beside the gain it tests, with the share of the gain it keeps and
  whether it falls back.
- `sql/metrics/backorder_exposure.sql`: the buyer's list for every held-back day. Each row is a part no center
  can ship that day, with its forecast calls and stuck calls before it ships and what the held-back days then
  brought. The plan's buyer items quote these rows.
- `sql/metrics/buyer_scorecard.sql`: whoever names the buyer's action, scored in dollars lost over the cheapest
  action: the engine's arithmetic, each Claude model, the fixed rules and the control. For a model it adds what
  asking cost, the wrong choices a misreading explains, and how many of its notes and replies are at fault.
- `sql/metrics/backorder_flag.sql`: the back-order flag on single bookings, scored on the held-back days:
  how many it marked, how many of those were stuck, and the forecast of the count.
- `sql/metrics/ai_step.sql`: each reading of the customer's words, its agreement with the booked code and the
  true problem, and the coverage of the list it leads to. It also counts the distinct phrasings the bookings
  hold, which is the real size of the sample.
- `sql/metrics/ai_calibration.sql`: how often Claude's first reading is right at each confidence it states.
- `sql/metrics/dispatch_scorecard.sql`: each way of handing out calls, with fixes, drive time, rolled calls,
  moved calls and cost a call.

## The pages

- `staple/report.py` builds the five pages from the query results, the week's plan and the code's own
  constants. The report is one page that scrolls: the scorecard beside the day's to-do list, then the day's
  board with the opened call beside it, then the evidence, then how to run it. It writes a hover hint for nearly every number, heading, bar, block and column, and stamps each page with the build that made
  it. The manual and the methods page quote every number, prompt, query and check straight from the code, so
  they cannot disagree with what ran, and a check refuses a numeral typed into either.
- `staple/pages/style.css` is the look: colors as named tokens, light and dark, the hover marker, the
  phone layout and the layout for paper.
- `staple/pages/tips.js` shows a hint on hover, keyboard focus or tap, moves the chart's crosshair, and runs the
  light/dark switch: every page opens light, and the browser remembers a reader who picks dark.
- `staple/pages/plan.js` switches the plan screen: the day, the chosen technician, the call opened beside the
  board, and the night plan or the day as it ran. It moves the arrow keys around the board, and opens every list
  for printing. Every day is already in the page; it only shows and hides.
- `staple/pages/explain.html` is the overview's text: the purpose, the key points and the functions, in few
  enough words to say aloud in ninety seconds. `check.py explain` holds it to that length, and refuses a
  function the report page does not name in a heading, a button or a header link.
- `staple/pages/manual.html` is the instruction manual's text, with a named gap wherever a value from the
  code goes.
- `staple/pages/method.html` is the methods page's text: every formula, rule, prompt and check, filled the
  same way.
- `staple/examples.py` holds one worked example for each section of the methods page that computes a figure:
  plain words, small made-up inputs, the steps worked by hand, and the same answer from the engine's own function.
  The manual prints the engine's answer, and `check.py examples` refuses one that disagrees with the hand answer.
- `staple/pages/languages.html` is the code languages page's text: what Staple is written in, where each part
  runs, the programs it starts and the files that are not code, with every count read from git's list of files.
- `staple/pages/card.png` is the share card a link preview shows, a photograph of the card page `report.py`
  writes, taken by headless Chrome with `python run.py --card`, a step outside Python. `staple/pages/card.sha`
  fingerprints the card page it was taken from, so `check.py report` refuses a card older than its words or colors.
- `staple/pages/board.png` is the README's picture of the board: one invented day as it ran, with a moved call
  open, under a strip that says the day is invented. `python run.py --picture` photographs it from the report
  last built. `staple/pages/board.sha` fingerprints the plan and the styles it was taken from, so
  `check.py report` refuses a picture older than the plan it shows.
- `docs/index.html`, `docs/explain.html`, `docs/manual.html`, `docs/method.html` and `docs/languages.html` are
  the published copy
  GitHub Pages serves.
  `python run.py --pages` writes them, and `check.py report` refuses a copy that differs from a fresh build.
  Beside them it writes `docs/card.png` and the tab icons, `docs/icon.svg`, `docs/icon-32.png` and
  `docs/apple-touch-icon.png`, which `report.py` draws from the stylesheet's own colors, and
  `docs/languages.json`, the file counts and the languages sentence, which the author's two websites read rather
  than typing the counts themselves.

## Repository plumbing

- `README.md` is the front page: what Staple does, how it is kept honest, how to run it, and what a real
  system must also handle.
- `WALKTHROUGH.md` is this page.
- `LICENSE` is the MIT license: anyone may reuse the code, keeping the copyright line.
- `.gitignore` keeps the built output, Python's caches and a private folder out of the repository.
- `.gitattributes` keeps shell scripts' line endings Unix-style, because bash fails on a Windows line ending.
- `.github/workflows/check.yml` has GitHub run `python run.py` and `python check.py` after every push, on
  Linux and on Windows, on the oldest and the newest Python the README names and one between. The mark
  beside each commit on GitHub is that run's result, so a stranger sees whether the two commands pass on a
  machine that is not the author's.
- `scripts/guard.sh` runs the repository's own guards before each commit: one test per mistake already made.
  `check.py plain-words` tries it on a scratch folder of planted guards and holds it to its arguments, its
  exit codes and the lines it prints.
- `scripts/guards/fast-budget` is the time a guard that runs before each commit is allowed, with the reason
  for it. The runner notes a guard that runs slower.
- `scripts/guards/fast/a-card-or-the-90-second-page-leaves-its-shape.sh` runs `check.py wording` and
  `check.py explain` on any commit that changes the report code, a page template or the checks. A card or a
  panel that does not open with its finding, and an overview too long to say aloud, are refused there.
- `scripts/guards/fast/the-guard-runner-keeps-its-arguments-and-exit-codes.sh` runs that check on any commit
  that changes the runner.
- `scripts/guards/fast/public-files-carry-no-private-names.sh` runs the package and private-names check on
  every commit, so nothing reaches the public repository without it. The same commit also recomputes every
  worked example, follows every manual link and holds the README and walkthrough to the files, because those
  checks need no built world.
- `scripts/guards/fast/a-guards-throwaway-repo-writes-into-the-commit-being-made.sh` makes sure a guard that
  builds a scratch repository can never write into the commit being made.
- `scripts/guards/fast/a-hover-tip-drops-when-a-finger-lifts.sh` taps every published page in an emulated
  phone, and the report's chart in an emulated tablet, before each commit that changes them: one tap must keep
  a hint open and the next tap must close it, with or without the browser moving focus. It runs a browser script the author
  keeps outside this repository, so it runs only on the author's machine and fails anywhere else, saying
  that nothing was checked.
- `scripts/guards/fast/first-screen-fits-a-laptop.sh` opens the published report in Chrome before each commit
  that changes it, in a frame the size a small laptop leaves for a page and again at a phone's width. The
  one-sentence summary and the whole scorecard must sit inside the laptop's first screen, nothing may run off
  the side on the phone, and every piece of text is read at the size it is drawn. A copy with planted faults,
  and the report from before the scorecard led the page, must each be refused. It needs Chrome and fails
  anywhere without it, saying that nothing was checked.
- `scripts/guards/fast/commit-messages-stay-plain.sh` reads every commit message on the branch and refuses
  one that carries a tool's trailer or a bookkeeping line, after proving on planted messages that it can.
