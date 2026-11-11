"""Four static pages: the report (index.html), its instruction manual, its methods page and its code languages page.

Every figure on an evidence card comes from a query in sql/metrics/, the plan is the dispatch replay's own
record, and every number the manual and the methods page quote is read from the code that uses it. The pages load nothing from the
network, carry a build stamp read from git, label every chart as made-up data, and give nearly every element a
hover hint with the data behind it.
"""
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import struct
import subprocess
import tokenize
import warnings
import zlib
from collections import Counter
from pathlib import Path

from staple import agent, buyer, db, dispatch, examples, load, metrics, plan, predict, triage, world

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(__file__).resolve().parent / "pages"
FILES = {"report": "index.html", "manual": "manual.html", "method": "method.html", "languages": "languages.html"}
PUBLISHED = ROOT / "docs"      # the copy the repository serves as its web page; `python run.py --pages` writes it
NO_HISTORY = "a copy with no saved history"   # what the stamp says where git cannot answer, such as a downloaded zip
STAMP_RE = (r"(?:Build [1-9]\d* · )?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [1-9]\d?, \d{4} "
            r"· [0-9a-f]{7,12}\+?")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEIGHT_LIMIT = 2_000_000    # bytes a published page may weigh: what a phone should load for one page
PAGES_URL = "https://acep2317.github.io/staple/"   # where GitHub Pages serves docs/, read from its API when turned on
LEDE = "Plans a repair fleet's next day: who takes each call, what each truck carries, and what people do before 08:00."
AUTHOR, SITE = "Ian Provencher", "https://ian-provencher.com/"    # who built it, and his own site
REPO = "https://github.com/AceP2317/staple"                        # where the code is kept
PHONE_COLUMN = 296    # px: the narrowest column a drawing gets, on a 360 px phone less the page's and the panel's edges
CARD, CARD_VERSION, CARD_SIZE = "card.png", 1, (1200, 630)   # bump CARD_VERSION whenever the card's pixels change
PICTURE, PICTURE_SIZE, PICTURE_SCALE = "board.png", (1000, 880), 2   # the README's picture: its size on the page, and the pixels drawn for each
PICTURE_STRIP = 44    # px: the strip across the top of that picture, which says the day is invented
STAPLE = [(7, 8, 11, 25), (7, 8, 25, 12), (21, 8, 25, 25)]   # the staple mark, as bars on a 32-unit square
SOURCES =["staple/", "sql/", "data/", "run.py"]    # what the pages' numbers are computed from
# Every file kind by its extension; `check.py languages` refuses a file whose kind neither map names.
CODE_LANGUAGES = {"py": "Python", "sql": "SQL", "js": "JavaScript", "sh": "Bash", "html": "HTML", "css": "CSS"}
FORMATS = {"json": "Data files (JSON)", "md": "Written notes (Markdown)", "png": "Pictures", "svg": "Pictures",
           "sha": "Fingerprints", "gitignore": "Git settings", "gitattributes": "Git settings",
           "yml": "Settings for GitHub's automatic check (YAML)", "": "Files with no extension"}
LANGUAGES_JSON = "languages.json"   # the counts and sentence, published beside the pages for the sites that quote them
PLACES = (   # where code runs, each with the languages that run there; `check.py languages` refuses a language in none
    ("browser", "In your browser", ("HTML", "CSS", "JavaScript"),
     "When you open a page, your browser draws it and runs its scripts. Each page carries its own styles and "
     "scripts, so it fetches nothing from any other site."),
    ("builder", "On the builder's computer", ("Python", "SQL", "Bash"),
     "Whoever builds Staple runs it here: python run.py invents the history and writes every result and page, python "
     "check.py runs every check, and the Bash guards run before each commit. On this machine that is Windows, with "
     "the Bash that Git for Windows ships."),
    ("github", "On GitHub's computers", (),
     "GitHub keeps the repository and serves the docs folder as the public pages. After each push, GitHub Pages runs "
     "Jekyll, its site builder, over that folder. Staple's pages carry no Jekyll settings, so it copies them as they "
     "are, and check.py fresh-clone proves it by comparing the live report with the committed copy. After each push "
     "GitHub also runs python run.py and python check.py on its own Linux and Windows machines, on the oldest and "
     "newest Python the README names and one between, and marks the commit with the result. Those runs change "
     "nothing in the repository."),
    ("anthropic", "On Anthropic's computers", (),
     f"{triage.MODEL_NAME} read every customer description once and wrote each follow-up question, and each model the "
     "buyer's card names decided every back-order case once. Staple sends them the prompts the methods page prints "
     "word for word, and none of Staple's code runs there. The answers are saved in the data folder, so the pages "
     "rebuild without a key; python run.py --live and python -m staple.agent ask the Claude API again with your own "
     "key."))
LANGUAGE_ABOUT = {   # what each language is, then what it does in this build
    "HTML": ("The language a web page is written in: headings, paragraphs, tables and links, which the browser lays "
             "out.", "The report's HTML is written by report.py. The manuals' text and this page's sit in HTML "
             "templates, each with a named gap where a value from the code goes."),
    "CSS": ("The rules for how a page looks: colors, spacing, and a layout for a phone.",
            "One stylesheet holds the light and dark colors as named tokens, and the build pastes it into every page."),
    "JavaScript": ("The language a browser runs inside a page, to answer a click, a tap or a key.",
                   "One script shows a hint on hover, focus or tap and runs the light and dark switch. The other "
                   "switches the plan screen's day, technician and view. Neither uses a framework or fetches anything."),
    "Python": ("A general programming language, run from a terminal. Staple uses only the library Python ships "
               "with, so nothing is installed.", "It invents the history, predicts parts, loads trucks, reads "
               "Claude's saved answers, replays dispatch, works the methods page's examples, writes the pages and "
               "runs every check."),
    "SQL": ("The language for asking a database a question. Python's own library carries SQLite, which keeps the "
            "whole invented world in one file and runs every query.", "One file defines the tables, and each metric "
            "query computes one table the report is built from."),
    "Bash": ("The command language of a Unix shell, run here by the Bash that Git for Windows ships.",
             "The guard runner starts the repository's guards before each commit, and each guard is one script."),
}
PROGRAMS = {   # every program the code starts, as `check.py languages` finds it in the code, and what it does here
    "git": ("git", "Lists the repository's files for these counts and the names scan, reads each page's build stamp "
                   "from the history, and clones the public repository fresh for check.py fresh-clone."),
    "chrome": ("Chrome", "Photographs the share card in python run.py --card and the README's picture of the board in "
                         "python run.py --picture, the build steps outside Python. One guard also opens the published "
                         "report in it, to see where the first screen ends."),
    "claude": ("Claude Code (claude)", "Built the saved answers and follow-up questions with python -m staple.triage "
                                       "build, on a subscription rather than a key."),
    "bash": ("Bash (bash)", "Runs the guard runner on a scratch folder of planted guards in check.py plain-words, which "
                            "holds the runner to its arguments, its exit codes and the lines it prints."),
}
e = html.escape

GLOSSARY = {
    "first-time fix": "A repair finished on the technician's first visit: every needed part was on the truck, and the repair held.",
    "carry list": "The parts a booked call is likeliest to need, ranked by the predictor. Its length is how many parts are on it.",
    "coverage": "The share of jobs whose every needed part was on the list or the truck. One missing part fails the whole job.",
    "held-back days": f"The last {world.TEST_DAYS} days of history. Methods learn from the days before and are scored on these.",
    "truck-day": "One technician's truck on one day: its load, its jobs and its costs.",
    "repeat visit": f"Another visit to a job the first visit did not fix, priced at ${load.REPEAT_VISIT:.0f} (made up).",
    "holding cost": f"The yearly cost of money tied up in truck stock: {load.HOLDING_RATE:.0%} of its value (made up).",
    "control": "A method run again with one link in its data broken, such as its history shuffled. A control that "
               "scores as well as the method shows the gain does not come from that link.",
    "lean": f"Where a model family has little history, its odds lean on the wider group, as if {predict.PULL} more jobs were seen there.",
    "regret insertion": "Placing first the call that loses most if its best technician's day fills up, not the call booked first.",
    "back-order": "A part the distribution centers cannot ship until a set day.",
    "booked code": "The problem code the booking recorded from the customer's words. Real history knows only this code.",
    "true problem": "What was really wrong. Known here only because the history is invented.",
    "unplanned call": f"A call added or moved on the day, {load.UNPLANNED:.0%} of calls (made up). No night-before list saw it.",
    "rolled call": "A call no technician's day had room for. It moves to tomorrow and counts as not fixed today.",
    "certified technician": "A technician allowed to carry and fit locked parts.",
    "locked part": "A part that may ride only on a certified technician's truck.",
    "follow-up question": f"The one question Claude asks when it is less than {triage.BLEND_BELOW:.0%} sure of the problem, "
                          "with a few answers for the customer to pick from. Made once by the model and saved.",
    "first-time fix chance": "The plan's chance a call is fixed on the first visit: the chance its truck holds every part the "
                          "call needs, times the technician's success rate on that appliance.",
    "load sheet": "One truck's list for the parts room tonight: each part to put on, take off or keep, with its worth and "
                  "the booked calls behind it.",
    "worth": f"What a part is worth carrying tomorrow: each booked call's chance of needing it times a ${load.REPEAT_VISIT:.0f} "
             f"repeat visit, plus a share for unplanned calls, less its holding cost. A ${load.MOVE:.0f} move counts against "
             "it when it is not aboard, and for it when it is.",
    "current rules": "What the invented fleet does now, and what every gain is measured against: each call goes to the "
                     "nearest qualified technician, driving today's fixed truck stock.",
    "parts cycle time": "The days a repair waits for its parts: from the visit that lacked one to the repeat visit that "
                        "brings it.",
    "parts-caused reschedules": "Calls whose truck lacked a needed part, so the repair is put off to a repeat visit "
                                "whatever the technician's skill. Counted per 100 calls served.",
    "fill rate": "Of the part lines the calls needed, the share already on the truck that came. A call that needs two "
                 "parts is two lines.",
    "inventory return": f"The repeat-visit dollars a year a truck's stock avoids, per dollar of stock aboard. A call counts "
                        f"when it needed a part and every part was aboard, at ${load.REPEAT_VISIT:.0f} a repeat visit (made up).",
    "call": "One repair visit to make. It starts as a booking, the customer's request in their own words, and once "
            "made it is a job in the history.",
    "decider": "Whatever names the buyer's action on a back-ordered part: the engine's arithmetic, a Claude model, a "
               "fixed rule or the control. Each is charged the action it names.",
}
COLUMNS = {
    "appliance": "The appliance type.", "jobs": "Jobs counted.", "repeat_visits": "Jobs not fixed on the first visit.",
    "parts_caused": "Repeat visits where a needed part was not on the truck.",
    "parts_caused_per_100_jobs": "Parts-caused repeat visits per 100 jobs of this appliance.",
    "parts_share_of_repeats_pct": "The share of this appliance's repeat visits that a missing part caused.",
    "k": "Parts on the carry list.", "predictor_pct": "Held-back jobs whose every needed part is on the predictor's list.",
    "national_pct": "The same, for the appliance's nationally most-used parts.",
    "shuffled_pct": "The same, for the predictor trained on shuffled history (control).",
    "leaked_pct": "The same, for the predictor with the held-back jobs already in its history. Its gap to the "
                  "predictor is what holding those days back is worth.",
    "todays_trucks_pct": "Held-back jobs whose every needed part was on today's truck.",
    "policy": "How the truck is loaded, or how calls are handed out. A row named for the history is what was "
              "recorded, under no policy.",
    "parts_per_job": "Carry-list parts per booked call; 0 where the policy uses no list.",
    "covered_pct": "Jobs whose every needed part was aboard.", "parts_aboard": "Parts on the truck, per truck-day.",
    "part_jobs_covered": "Jobs a truck-day that needed a part and had every one aboard. A job that needed none is left out.",
    "space_used": "Units of truck space the load takes, per truck-day.",
    "permanent_parts": f"Parts aboard on at least {load.PERMANENT:.0%} of a truck's days.",
    "moves_a_night": "Parts put on or taken off overnight, per truck.",
    "stock_value": "Dollars of stock on board: money tied up, not spent.",
    "repeat_cost": f"Repeat visits for jobs the load missed, at ${load.REPEAT_VISIT:.0f} each, per truck-day.",
    "handling_cost": f"Parts moved overnight at ${load.MOVE:.0f} each, or at the price the row names, per truck-day.",
    "holding_cost": f"Stock value times {load.HOLDING_RATE:.0%} a year, per day.",
    "cost_a_truck_day": "Repeat, handling and holding together, per truck-day.",
    "roi": "Repeat-visit dollars a year the stock avoids, per dollar of stock on board, counting only jobs that needed a part.",
    "control": "The control: the method run again with one link in its data broken.",
    "gain_tested": "The gain this control tests.", "measure": "What the row's figures count.",
    "unit": "What one figure of the measure is counted in.",
    "better_when": "Whether a higher or a lower figure is the better one.",
    "current_rules": "The measure under the current rules: the nearest technician, on today's stock.",
    "staple": "The measure under Staple: skills and parts, on balanced loads.",
    "difference": "Staple's figure less the current rules', from the two as shown.",
    "control_keeps_pct": "The share of the difference the control keeps, from the three figures as shown.",
    "without_it": "The measure without the method.", "with_it": "The measure with the method.",
    "with_control": "The measure with the control in the method's place.",
    "gain_kept_pct": "The share of the gain the control keeps, from the three figures as shown. Below zero, it lands "
                     "worse than where the method started.",
    "verdict": f"Falls back when the control keeps under {metrics.FALLS_BACK:.0%} of the gain.",
    "calls": "Held-back calls.", "rolled": "Calls rolled to tomorrow; each counts as not fixed.",
    "first_time_fix_pct": "Expected first-time fixes per call, a rolled call counting zero.",
    "parts_missing_pct": "Visits where a needed part was not on the truck.",
    "drive_minutes": "Drive minutes per call served.", "moved": "Calls moved to another truck by a sick day or breakdown.",
    "moved_fix_pct": "First-time fixes among the moved calls.",
    "cost_per_call": f"Minutes at ${dispatch.MINUTE:.0f}, plus repeat visits at ${load.REPEAT_VISIT:.0f}, per call served.",
    "method": "How the customer's words were read.", "agrees_booked_pct": "Readings that match the booked code.",
    "agrees_true_pct": "Readings that match the true problem, known only because the data is invented.",
    "coverage_pct": "Jobs whose every needed part is on the 3-part list built from the reading.",
    "phrasings": "Distinct customer descriptions among these jobs. The invented customers say each problem a few ways.",
    "stated_confidence": "The band of confidence Claude stated with its first reading, from its lower edge up to the next.",
    "staple_then": f"What Staple does with a reading this sure: below {triage.BLEND_BELOW}, Claude asks its one question.",
    "day": "The held-back day this list is for.", "part": "The back-ordered part.",
    "ships_in_days": "Days from that day until a center can ship it.",
    "back_test": "1 on the first day a back-order is listed, when it ships inside the held-back days. The forecast is scored on these rows.",
    "trucks_holding": "Trucks that stock one today.",
    "open_zones": f"Zones with no holding truck based within {load.NEARBY_MINUTES} minutes.",
    "expected_calls": "Calls forecast to need it before it ships, from training history.",
    "expected_stuck": "Of those, calls forecast to have no truck nearby holding one.",
    "dollars_at_risk": f"Expected stuck calls times ${load.REPEAT_VISIT:.0f}.",
    "actual_calls": "Calls the held-back days then brought.", "actual_stuck": "Of those, calls no truck nearby could cover.",
    "bookings": "Held-back bookings scored.",
    "forecast_stuck": "The sum, over every booking, of its odds of needing a part no center can ship and no truck nearby holds.",
    "were_stuck": "Bookings that did need such a part.",
    "flagged": f"Bookings whose odds of needing such a part reach {load.BACKORDER_FLAG:.0%}, the flag's level.",
    "flagged_and_stuck": "Flagged bookings that did need such a part.",
    "flags_right_pct": "The share of flagged bookings that did need such a part.",
    "repairs": "Repairs counted: every held-back repair in the history row, the calls served in a replayed row.",
    "repairs_waiting_pct": "Repairs whose truck lacked a needed part, so they wait for it.",
    "waits_on_backorder_pct": "Of the repairs that wait, those lacking a part no center could ship on the day of the visit.",
    "days_when_waiting": "Days from the visit to the repeat visit, for a repair that waits.",
    "days_a_repair": "The same days spread over every repair, waiting or not.",
    "decider": "Who names the action: the engine's arithmetic, a Claude model's saved decisions, a fixed rule or the control.",
    "decisions": "Cases decided: every back-ordered part of every held-back day.",
    "could_not_be_done": "Choices that could not be carried out, and cases left undecided. Each is charged as sending anyway.",
    "chose_cheapest": "Choices that cost what the cheapest action costs.",
    "one_misreading_explains": "Wrong choices that are the cheapest once expediting is charged the calls that come after "
                               "the early ship day, where the rule charges those before it.",
    "cost_of_choices": "Expected dollars of the actions named, over every case.",
    "dollars_lost": "Those dollars less what the cheapest action costs on each case.",
    "lost_a_decision": "Dollars lost, spread over every decision.",
    "asking_dollars": "What the Claude API charged for a model's decisions, from the token counts saved with each, at "
                      "the listed prices. A rule asks nothing.",
    "asking_cents_a_decision": "The same, in cents a decision.",
    "notes_short": "A model's notes that leave out the part, its ship day or a truck of a move.",
    "replies_run_on": "A model's decisions whose reason or note runs on into stray markup.",
}
MARK_HELP = {
    "needs a certified technician": ("The part is locked and this technician is not certified.", "Send a certified technician."),
    "aboard": ("The technician's own truck holds it.", "Nothing."),
    "from a center overnight": ("A center can load it before the visit.", "Load it tonight."),
    "on a truck nearby": (f"A truck based within {load.NEARBY_MINUTES} minutes holds it.", "Hand it over, or send that truck."),
    "at a center, not loaded": ("A center holds it, and tonight's load left it off this truck.",
                                "Nothing: its odds did not earn the space. It can be loaded for another night."),
    "back-ordered": ("No center can ship it in time and no truck nearby holds it.", "Expedite, source elsewhere, or move a spare."),
}
CHECK_ABOUT = {
    "guards": "The code imports nothing outside Python's own library, names no private name in a public file, and writes no f-string that only Python 3.12 or newer can read. A planted import and two planted f-strings must be refused. The names scan needs a list kept out of the public repository, so a copy without it says the scan was skipped.",
    "weight": f"Each published page stays under {WEIGHT_LIMIT // 1_000_000} MB, the most a phone should load for one page. A page padded past the limit must be refused.",
    "banned-control": "A name planted in a scratch file must be caught by the names scan, and a longer word containing a name must not be.",
    "predictor": "One well-populated and one thin cell are recomputed by hand from the three levels' counts. The thin cell must sit near the wider odds with the pull, and far from them without it.",
    "backtest": "Coverage on the held-back jobs. The predictor must beat the national list, and the lean must help the newest families.",
    "control": "The predictor trained on shuffled history must score no better than the national baseline.",
    "dollars": "Every load policy's costs, with planted loads recomputed by hand. No load may break the space, lock or back-order rules, and a planted bad load must show all three faults. The balanced truck must be the cheapest load, and the back-order flag must be calibrated.",
    "backorders": "The back-order list for every held-back day, held to the calendar. Each part must be listed on every day from a back-order's start to the day before it ships, with the days left falling by one a day, and on no other day. The forecast is scored once for each back-order that shipped inside the held-back days, and must sit within three Poisson spreads of what came. A count that does not fall, a day left out, a part still listed the day it ships, and a forecast three times too high must all fail.",
    "replay-clock": "The replay obeys the clock and the calendar. No call that came in during the day, and none moved by a breakdown, may start before that minute, read from the day's own events. No truck may take on a part no center can ship or send one back before it ships, and a part must be loaded again once it ships. A list read before 08:00 may name no call that has not come in, the buyer's list must be the back-order query's own rows for its day and count down a day at a time, and every technician with no booked calls must be named. Six planted faults must be refused.",
    "triage": "The AI step on its cached answers, live mode against a local stand-in for the API, and its controls: shuffled answers and random replies must fall back.",
    "triage-honest": "The AI step says what it is. No customer sentence may repeat a word or open with a second subject, and every phrase filed as vague must be one Claude itself is unsure of. The report must give the count of distinct phrasings behind its scores, call the score after the follow-up question an upper bound, and show how often Claude is right at each confidence it states. In every band Claude must be right at least as often as the confidence it stated, and in the lowest band seldom right, and shuffled answers must break that. No public file may describe the keyword matcher as the way bookings are coded now. A live run against a local stand-in must carry its answers into the week's plan, which can no longer load answers of its own. A rejected key, a reply that is not JSON, a malformed answer, a reply cut short and an API that cannot be reached must each stop with one plain sentence. The old phrasings, a vague phrase read with confidence, a changed count of phrasings and a run that loads its answers twice must all be refused.",
    "plain-words": "The public copy holds no word from the author's private setup, and the guard runner keeps its contract. The private list of names and words is scanned over every file git would publish. Each entry is then planted in a copy of the README in turn and must be caught, so the list holds no entry the scan cannot see; a copy with no list tries the scan on stand-in words and says so. The runner is tried on a scratch folder of planted guards: one that passes, one that fails, an argument it does not know, an empty folder, a parked guard that now passes, one that still finds its mistake and one that is broken, a guard that hangs, a guard slower than its allowance, and guards chosen by the files a commit stages. A copy of the runner that lets a failing guard through must be refused.",
    "corners": "The corners cut on purpose are read from the comments that mark them. Only a real comment counts, so the marker written inside a string is not a corner: every corner found must be a line that opens with the marker, a planted file with the marker in a string and in a comment must give one corner, and a comment that runs on must be read whole. Every corner must name its upgrade. The methods page must list each corner once and write the marker once, where it defines it, and the report must state the count the method cuts. A page with one corner dropped, and a report with the wrong count, must be refused.",
    "wording": "The words match the code. No numeral may be typed into a page template: every figure on the manual and the methods page is read from the code. No public file may hold a British spelling, a metric distance or a day-first date. No page or note may call a thing by a dropped name, say 1 with a plural, show a cost a call or a truck-day short of the cent, or claim that every figure comes from a query. Each row of the day's comparison must add up from the figures as shown and say a tie in words, each column under a chart must read at one precision, a part name that more than one appliance has must carry its appliance, and the back-order card must quote the sums of its own rows. Each rule has a planted fault that must be refused. Numbers written as words are not scanned.",
    "dispatch": "Every dispatch policy replayed. Staple must beat the nearest technician on fixes and on cost, and be cheapest on its own cost. Zero rule breaks, and a planted bad visit must show all four kinds.",
    "dispatch-blind": "Each technician's success rate is learned from the training history, never read from the table the made-up world was written from. That table is scrambled after the history exists, and every visit must stay with the same technician at the same time. A technician left with five visits must match the pull toward the appliance's average worked by hand, and a history rewritten against one technician must move the plan.",
    "dispatch-control": "Shuffled skills must lose at least half the skill gain. Shuffled parts odds must stay within a point, and the reason the report gives for that is measured: odds that know only the appliance must score within half a point of the shuffled ones, and the parts odds handed over for same-day calls alone must keep more than half the gain. No control may win by overbooking the day.",
    "controls-shown": "Every control and test the engine runs is on the report with its real result. No query may leave out a row of the results table it reports, and every row the engine names a control must have a row in the controls query. The report's cards must carry the figures of the balanced truck's three tests, the leak test and each control, and the words they rest on must match the query's verdicts. The return on stock must leave out jobs that needed no part, and its table must run from the cheapest truck-day up. Held to today's space, no load may take more space than today's stock, and at a prohibitive price for a move the balanced truck must move nothing and cost what today's truck costs. A query that hides the shuffled rows, a control with no row, a figure changed on the page and a verdict turned over must all be refused.",
    "plan": "The week's plan, coded by Claude and replayed. Every call carries Claude's code and every unsure call its question and answer; every booked call appears once; no route runs past 17:00; loads stay within space, locks and back-orders; day totals equal the replay's; each moved call names where it came from and went; and the sick calls and breakdowns equal the 92-day run's on the same days. Each rule has a planted fault that must be caught.",
    "sql": "Every metric query returns rows, and every column of every query is recomputed in Python apart from the SQL. A decimal may differ by half a unit of its last shown place, which is all rounding can do, and the return on today's trucks is counted again from the jobs and the stock. Planted changes in a copy of the data must move the history metrics, and a part taken off a truck must move that recount.",
    "mutants": "Every metric query against one wrong version of itself, such as a fix rate that counts the wrong column or a return on stock that counts jobs needing no part. The Python recomputation that agrees with the real query must refuse each wrong one, and a query added without a wrong version fails the check.",
    "stamp": "The build stamp is read from git alone. On a scratch history it must give the count, day and code of the last change outside the published copy; committing only that copy must leave it alone; unsaved code must add a +; a partial history must drop the count and warn; and a folder with no history must say so. Two builds that differ only in their stamps must compare equal once the stamps are emptied.",
    "report": "The pages carry the stamp, the results day, a made-up label and a table per chart, a hint on every element that needs one, and nothing from the network. Each page opens light with a switch to dark, and every selected mark stands out at least 3:1 from the page in both. Each page carries its tab icons and share card, the icons as drawn now and the card photographed from today's words. The README's picture of the board must have been photographed from today's plan and styles. The published copy must match a fresh build, and planted faults must be refused.",
    "readable": "The pages can be read and used. No text set for the screen is under 14 px, and no label on the board under 12 px; a drawing's text is measured at the least width the drawing can take. In light and in dark, text reaches 4.5:1 against what it sits on and every mark a reader must tell apart reaches 3:1. On every day and view the report's Tab order holds fewer than 150 stops, because the board is one stop and the arrow keys move inside it. Every switch says whether it is pressed. Printing hides the controls and opens the lists. Every page names the author with a link to his site and to the repository, and links to no other site. On a phone the scorecard folds its control under the difference, a click opens a call beside the board, and each day's calls are given as those booked the night before plus those added on the day. Nineteen planted faults must be refused, among them a text size too small, a color too pale in one scheme only, every hint put in the Tab order and a link to another site.",
    "docs": "The README carries the values the code holds (job count, held-back days, seed, model, live address). In it and the walkthrough, every number must sit inside a phrase the code itself writes, so no result is typed by hand; a share or a multiple spelled as words is refused too. The walkthrough names every published file, and every relative link resolves. Six planted faults must be refused, each by its own rule: a percent, a dollar figure, a bare number, a spelled multiple, an unnamed file and a dead link.",
    "examples": "Every worked example on the methods page is recomputed by the engine's own function and must match the answer worked by hand, and the page must draw each one in its section as words, inputs, steps, Staple's answer, then the formula. A hand answer nudged by 0.001 and a formula drawn first must both be refused.",
    "links": "Every link from the manual into the methods page, and every link within a page, lands on an id the page holds. A manual link turned to an anchor that does not exist must be refused.",
    "languages": "Every file git would publish is a code language or a format Staple's map names. Every language held has a section on the code languages page, every program the Python code starts has a purpose line, and the published languages.json matches a fresh count and the published page. A planted .rb file, a language with no section, a planted start of another program and a JSON with one count off must all be refused, and a folder with no git must be walked with a warning.",
    "first-run": "A stranger's first run, offline. Every file git would publish is copied to an empty folder; the one command and every check must pass there with an empty home folder, no Claude key and every web request sent to a dead address. The run must say which parts it skipped, and damaging its published copy must make its own report check fail. It runs the whole suite, so it runs only when named.",
    "fresh-clone": "The publishing check. The public repository is cloned as a stranger would clone it, at this commit, with an empty home folder. The one command and every check must pass there, the clone's fresh build must equal its published copy with the stamp included, and the live report must equal that copy too. Damaging the copy must make the clone's own report check fail. It needs the network and runs the whole suite, so it runs only when named.",
    "history-dates": "The history records the order day and the repeat-visit day of each repair that failed for a missing part, and adding them moved nothing else. The world is built with and without the two days, and every other column of every table must hold the same values; a build with one value changed must be refused. Every repair that lacked a part has both days and no other repair has them. The order falls on the visit day or the next, the repeat visit comes after it by one of the made-up lead times, and a part on back-order arrives only after a center can ship it. The shares of each lead time and of next-morning orders must match the made-up table within a point. A repeat visit moved onto its order day, a repair with its parts given a wait, a wait taken away, an order placed two days late, a wait of a length the table does not hold and a part that arrives the day its back-order ends must each be refused, on the rules it breaks and no other.",
    "cycle-time": "Parts cycle time, the days a repair waits for its parts. The replay gives each repair that lacks a part the mean wait under the rule the history was written by. Over every recorded wait that mean must sit within four standard errors of the recorded days, and an engine that orders every part on the day of the visit must be refused. The same mean is worked a second way, day by day, and must agree on every repair; a repair that lacks two parts must wait at least as long as for either part and less than for both in turn. Two replays are counted again from their recorded visits and loads and must give the replay's own figures, and emptying the back-order calendar must shorten the wait. The report says a waiting repair waits longer under Staple because of back-orders, so back-orders must make up most of that gap. Three planted changes must move the query's rows, a changed figure must reach the card, two builds whose query contradicts the card's words must stop, and the worked example must match its hand answer.",
    "scorecard": "The five-measure scorecard is what the replay gives. The raw figures behind it are counted again from the recorded visits and loads of its three plans, the current rules, Staple and Staple on shuffled history, and must equal the replay's own; a recount with one part taken off every truck must move. Every measure must be the better under Staple, the difference must be the difference of the two figures shown beside it, and the control must keep most of each gain and less than all of it, because the words under the table say so. Three planted changes must move the query's rows as they should. The report must carry the scorecard once, with every figure of every row as the query gives it; a page with one figure changed must be refused, and a build whose query makes a measure worse under Staple, or lets the control pass Staple, must stop.",
    "buyer-scorer": "The buyer's four actions on every back-ordered part of every held-back day. The four prices are worked again apart from the engine, the waiting calls are recounted from the bookings, and every single move is tried again from the tables. A decider that takes the cheapest action every time must lose nothing, one wrong choice must lose exactly its gap, and a choice that cannot be carried out must be charged as sending anyway and counted. Each fixed rule and the control must lose money.",
    "buyer-loop": "Claude as the buyer, driven offline by a scripted stand-in. The stand-in sees only what a request holds. It asks for the lookups, prices the four actions from their answers, and must name the engine's cheapest action on every case; a stand-in that never asks about the trucks must not. Each lookup's answer must be exactly the case's own counts, days and odds, and a lookup that also gives a dollar figure must be refused. A call about another part, an action that is not one of the four and several other malformed calls must each be answered with an error. A conversation that never commits must stop at the round limit and be scored as sending anyway. Over a local stand-in for the API, decisions must be saved by model and prompt, a second run must ask for nothing, a failure part way must keep what was answered, and each failure must stop the run in one plain sentence. The spending cap must refuse a request that could pass it, hold room for a request still out, and keep the cost of a case cut short. The manual must name every command that can reach the Claude API.",
    "buyer": "Claude's saved decisions as the buyer, as the report shows them. Each scored model's row is worked again from its saved file apart from the engine: what its choices cost and lost, the choices that could not be carried out, the wrong choices one misreading explains, what asking cost from the saved token counts, the notes that leave out the part, its ship day or a truck of a move, and the replies that run on into stray markup. One choice turned to the costliest action must lose exactly its gap. A note with its ship day cut or moved a day and a reason that runs on into a tag must each be counted, and a ship day written in words must not be. A saved file short of a decision, or made with another prompt, must be left unscored and named. The card must carry every decider's figures as the query gives them: a card built from a changed figure must be refused, a build whose query lets a fixed rule lose nothing must stop, and with no model's rows the card must say none is scored. Each day's buyer items must carry the leading model's saved action, reason and note, say what a note leaves out, cut a reply where it runs on and say so, and name the action the engine prices cheaper where there is one.",
    "data": "Two builds from one seed must match. Planted noise sits at its rates; no part belongs to another appliance, no locked part rides uncertified, no truck runs part-empty, the newest families are thin, and furnace calls follow the seasons.",
}


SAMPLE_Q = "A blue ?\nSits beside a word the manual's glossary explains. Clicking it opens the manual in a new tab at that word."


class Raw(str):
    """Markup already escaped, passed through as it is."""


def join(*parts):
    return Raw("".join(p if isinstance(p, Raw) else e(str(p)) for p in parts))


def tipattr(text, focus=False):
    """The hint an element shows on hover or tap. focus also puts it in the Tab order, for what a reader acts on."""
    return f' data-tip="{html.escape(text, quote=False).replace(chr(34), "&quot;")}"' + (' tabindex="0"' if focus else "")


def hv(shown, tip):
    return Raw(f'<span class="hv"{tipattr(tip)}>{e(str(shown))}</span>')


def slug(term):
    return "term-" + re.sub(r"[^a-z]+", "-", term.lower()).strip("-")


def q(term):
    return Raw(f'<a class="q" href="manual.html#{slug(term)}" target="_blank" rel="noopener"'
               f'{tipattr(term.capitalize() + chr(10) + GLOSSARY[term] + chr(10) + "The ? opens the manual at this word.")}>?</a>')


def pct(v):
    return f"{v:.1f}%"


SICK_AT = plan.clock(dispatch.SICK_BY)    # the hour sick calls are known, as the pages write it
PYTHON = (3, 10)                          # the oldest Python the checks have run on


def dollars(v):
    """A price or a stock value, which are whole dollars."""
    return f"${v:,.0f}"


def cents(v):
    """A rate or an expected amount, such as the cost a call, always to the cent."""
    return f"${v:,.2f}"


def gap(x, y, places):
    """The difference of two figures as the page shows them, so a reader can check the three against each other."""
    return round(round(x, places) - round(y, places), places)


def fewer(saved):
    """How a day's repeat visits compare with the current rules, in words that hold for a gain, a tie and a loss."""
    if saved > 0:
        return f"{saved:.1f} fewer repeat visits than the current rules"
    if saved < 0:
        return f"{-saved:.1f} more repeat visits than the current rules"
    return "as many repeat visits as the current rules"


def some(v):
    """A whole count inside a sentence, with none said in words."""
    return f"{v:.0f}" if round(v) else "none"


def places(values):
    """The decimal places one table column shows: the most any of its numbers needs, so a column reads at one precision."""
    return max((len(repr(v).split(".")[1]) for v in values if isinstance(v, float) and v != int(v)), default=0)


def rows(results, name):
    columns, data = results[name]
    return [dict(zip(columns, r)) for r in data]


# The verdict each card's words rest on. A build whose controls query says otherwise stops, so no page says a
# control fell back when it did not.
WORDS_REST_ON = {"shuffled-history control": "falls back",
                 load.CONTROL: "does not fall back",
                 load.CONTROL_BALANCED: "does not fall back",
                 dispatch.SHUFFLED_SKILLS: "falls back",
                 dispatch.SHUFFLED_ODDS: "does not fall back",
                 dispatch.SHUFFLED_HISTORY: "does not fall back",
                 "Claude answers shuffled (control)": "falls back",
                 triage.RANDOM_REPLIES: "falls back"}


def kept(row):
    """The share of a gain one control keeps, as the report says it."""
    return f"{row['gain_kept_pct']:.0f}%" if row["gain_kept_pct"] > 0 else "none"


def about(name):
    """The definition a metric query opens with, read from the file itself."""
    head = [ln[2:].strip() for ln in (metrics.SQL / f"{name}.sql").read_text(encoding="utf-8").splitlines()
            if ln.startswith("--")]
    return " ".join(head)


def git_answer(root, *args):
    """What git prints for this folder, or None when git is missing, fails, or holds no history here. Inside a
    commit hook git's own variables point at the repository being committed whatever -C says, so they are dropped."""
    env = {k: v for k, v in os.environ.items()
           if not re.fullmatch(r"GIT_(DIR|INDEX_FILE|WORK_TREE|PREFIX|OBJECT_DIRECTORY|COMMON_DIR)", k)}
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def pretty_day(day):
    """2026-11-06 written as Nov 6, 2026."""
    year, month, date = day.split("-")
    return f"{MONTHS[int(month) - 1]} {int(date)}, {year}"


def stamp(root=ROOT):
    """(stamp, the day the pages' sources last changed, the stamp pattern, warnings), all read from git.

    The stamp names the last change outside docs/, the published copy: how many changes the history held at
    that change, the day it was made, and its short code. Committing the published copy therefore leaves the
    stamp alone, so a fresh clone of the published repository builds the stamp its live pages carry. A + marks
    work not yet committed. Where git holds no history, the stamp says so and the run prints why."""
    outside = [".", f":(exclude){PUBLISHED.name}"]
    last = git_answer(root, "log", "-1", "--format=%H %cs", "--", *outside)
    if not last:
        return NO_HISTORY, None, None, [f"git holds no history for this folder, so the pages say '{NO_HISTORY}'"]
    commit, day = last.split()
    found, number = [], None
    if git_answer(root, "rev-parse", "--is-shallow-repository") == "false":
        number = git_answer(root, "rev-list", "--count", commit)
    else:
        found.append("this copy holds only part of its history, so the stamp carries no build number; "
                     "`git fetch --unshallow` fetches the rest")
    unsaved = "+" if git_answer(root, "status", "--porcelain", "--", *outside) else ""
    text = (f"Build {number} · " if number else "") + f"{pretty_day(day)} · {commit[:7]}{unsaved}"
    saved = git_answer(root, "log", "-1", "--format=%cs", "--", *SOURCES)
    changed = dt.date.today().isoformat() if git_answer(root, "status", "--porcelain", "--", *SOURCES) else saved
    return text, pretty_day(changed) if changed else None, STAMP_RE, found


SCORE_SHOWS = {   # each unit the scorecard query names: how a figure in it reads, and how a difference does
    "percent of calls": ("{:.1f}%", "{:.1f} points"), "percent of part lines": ("{:.1f}%", "{:.1f} points"),
    "per 100 calls served": ("{:.1f}", "{:.1f}"), "days a repair": ("{:.2f}", "{:.2f}"),
    "dollars a year per dollar of stock": ("${:.2f}", "${:.2f}"),
}


def scored(row, column):
    """One scorecard figure as the page writes it: in its measure's own unit, and a difference with its sign."""
    figure, change = SCORE_SHOWS[row["unit"]]
    if column != "difference":
        return figure.format(row[column])
    return ("+" if row[column] > 0 else "−") + change.format(abs(row[column]))


def scorecard(results):
    """The five measures of the whole plan across the held-back days: the current rules, Staple, the difference and
    the control. The words under the table rest on the figures, so a build whose query no longer supports them stops."""
    score = rows(results, "scorecard")
    keeps = [r["control_keeps_pct"] for r in score]
    worse = [r["measure"] for r in score if not r["difference"] or (r["difference"] > 0) != (r["better_when"] == "higher")]
    if worse or not 50 < min(keeps) <= max(keeps) < 100:
        raise ValueError("the scorecard says Staple is the better on every measure and that its control keeps most of each "
                         f"gain and less than all of it; the query now says otherwise ({worse or keeps}): rewrite the "
                         "scorecard's words before publishing")
    head = "".join(f"<th{' class=' + chr(34) + 'ctl' + chr(34) if name == 'Control' else ''}{tipattr(name + chr(10) + hint)}>{e(name)}</th>"
                   for name, hint in (
        ("Measure", "What the row counts, and the unit it is counted in."),
        ("Current rules", GLOSSARY["current rules"]),
        ("Staple", "Each call goes to the technician with the lowest expected cost, on a truck loaded for the calls booked on it."),
        ("Difference", "Staple's figure less the current rules', from the two figures as shown."),
        ("Control", f"{dispatch.SHUFFLED_HISTORY}: Staple with the history behind its part odds shuffled. Under its figure "
                    "is the share of the difference it keeps.")))

    def line(r):
        name = r["measure"]
        about_it = (f"{name.capitalize()}\n{GLOSSARY[name]}\n{'Higher' if r['better_when'] == 'higher' else 'Lower'} is better. "
                    f"Counted in {r['unit']}.")
        kept = (f"{dispatch.SHUFFLED_HISTORY}: {scored(r, 'control')}\nThe current rules {scored(r, 'current_rules')}, Staple "
                f"{scored(r, 'staple')}. The control keeps {r['control_keeps_pct']:.0f}% of that difference.")
        return (f'<tr><td class="m"{tipattr(about_it)}><span class="hv">{e(name.capitalize())}</span>{q(name)}'
                f'<span class="unit">{e(r["unit"])}</span></td><td>{e(scored(r, "current_rules"))}</td>'
                f'<td class="ours">{e(scored(r, "staple"))}</td><td class="gain">{e(scored(r, "difference"))}</td>'
                f'<td class="ctl"{tipattr(kept)}><span class="hv">{e(scored(r, "control"))}</span>'
                f'<span class="keeps">keeps {r["control_keeps_pct"]:.0f}%</span></td></tr>')

    spread = f"{min(keeps):.0f}%" if min(keeps) == max(keeps) else f"{min(keeps):.0f}% to {max(keeps):.0f}%"
    title = f"The scorecard · all {world.TEST_DAYS} held-back days, on the booked codes"
    tip = (f"The scorecard\nSource: sql/metrics/scorecard.sql. {about('scorecard')}\nIt runs on the call-taker's booked "
           "codes, so it measures the planning apart from the reading.")
    madeup = (f"Invented history\n{world.JOBS:,} jobs from seed {world.SEED}. No real company, customer, part or technician. "
              "The method is real; these figures are not.")
    return (f'<section class="panel score-panel" id="scorecard"><h2{tipattr(tip)}>{e(title)}</h2>'
            f'<p class="sub">Every row is the whole plan replayed with the same made-up sick calls and breakdowns. '
            f'<span class="invented"{tipattr(madeup)}>made-up data</span></p>'
            f'<div class="scroll"><table class="score"><thead><tr>{head}</tr></thead><tbody>{"".join(line(r) for r in score)}'
            f'</tbody></table></div><p class="sub after">The control is Staple with the history behind its part odds shuffled, so it knows what '
            f'each appliance usually needs and nothing finer. It keeps {spread} of each gain. So most of each gain needs '
            f'only what each appliance usually needs, and the history\'s links to model family and problem add the rest.</p>'
            '</section>')


def bars(items):
    """Ranked horizontal bars; the row the story is about wears the accent. Items: (label, value, shown, tip, kind)."""
    top = max(v for _, v, *_ in items)
    return '<div class="bars">' + "".join(
        f'<div class="bar-row {kind}"{tipattr(tip)}><span class="bar-label">{e(label)}</span>'
        f'<span class="track"><span class="bar" style="--v:{value / top:.3f}"></span>'
        f'<span class="bar-value">{e(shown)}</span></span></div>' for label, value, shown, tip, kind in items) + "</div>"


def figure(name, title, chart, result, row_tip=None, shown=None):
    """A chart and the table under it, which opens on a click. shown, when given, is the part of the query's rows
    the table prints."""
    columns, every = result
    data = every if shown is None else shown
    scope = (f"{plan.counted(len(data), 'row')} from sql/metrics/{name}.sql." if shown is None else
             f"{len(data)} of the {len(every):,} rows sql/metrics/{name}.sql returns: this week's days.")
    exact = ("exactly as" if shown is None else "this week's days, as")
    head = "".join(f'<th{tipattr(c + chr(10) + COLUMNS[c])}>{e(c).replace("_", "_<wbr>")}</th>' for c in columns)
    at = [places([r[i] for r in data]) for i in range(len(columns))]

    def cell(i, v):
        return f"{v:.{at[i]}f}" if isinstance(v, float) else str(v)
    body = "".join("<tr>" + "".join(
        f'<td{tipattr(row_tip(dict(zip(columns, r))))}><span class="hv">{e(cell(i, v))}</span></td>' if i == 0 and row_tip
        else f"<td>{e(cell(i, v))}</td>" for i, v in enumerate(r)) + "</tr>" for r in data)
    numbers = (f"Show the numbers\nThe {plan.counted(len(data), 'row')} {'this chart is drawn from' if chart else 'of this table'}, "
               f"{exact} sql/metrics/{name}.sql returns {'it' if len(data) == 1 else 'them'}.")
    madeup = (f"Invented history\n{world.JOBS:,} jobs from seed {world.SEED}. No real company, customer, part or "
              "technician. The method is real; this figure is not.")
    return (f'<figure><figcaption><span class="fig-title"{tipattr(title + chr(10) + about(name) + chr(10) + scope)}>'
            f'{e(title)}</span> <span class="madeup"{tipattr(madeup)}>made-up data</span></figcaption>{chart}'
            f'<details><summary{tipattr(numbers)}>'
            f'Show the numbers</summary><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
            f'</table></div></details></figure>')


def curve(points, jobs):
    """Coverage against carry-list length: three lines, the axis carrying the values, a crosshair for hover."""
    w, h, left, right, top, bottom = 560, 300, 56, 18, 16, 48
    ks = [p["k"] for p in points]
    x = [left + i * (w - left - right) / (len(ks) - 1) for i in range(len(ks))]

    def y(v):
        return top + (100 - v) * (h - top - bottom) / 100

    series = [("predictor_pct", "Predictor", "--s1", "Ranks parts by how often they fixed the same model family and problem, leaning wider where history is thin."),
              ("national_pct", "National top parts", "--s2", "The appliance's nationally most-used parts, whatever the problem: today's stocking logic."),
              ("shuffled_pct", "Shuffled history (control)", "--dim", "The predictor trained on history whose part-to-problem links were shuffled. It must fall to the national line.")]
    today = points[0]["todays_trucks_pct"]
    svg = [f'<line class="gridline" x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
           f'<text class="tick" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v}%</text>' for v in (0, 25, 50, 75, 100)]
    svg += [f'<text class="tick" x="{xi:.1f}" y="{h - bottom + 20}" text-anchor="middle">{k}</text>' for xi, k in zip(x, ks)]
    svg.append(f'<text class="tick" x="{(left + w - right) / 2:.1f}" y="{h - 6}" text-anchor="middle">parts on the carry list</text>')
    svg.append(f'<line class="ref" x1="{left}" x2="{w - right}" y1="{y(today):.1f}" y2="{y(today):.1f}"/>'
               f'<text class="ref-label" x="{left + 8}" y="{y(today) - 7:.1f}">today\'s trucks: {pct(today)}</text>')
    for key, _, color, _ in series:
        line = " ".join(f"{xi:.1f},{y(p[key]):.1f}" for xi, p in zip(x, points))
        svg.append(f'<polyline class="series" style="stroke:var({color})" points="{line}"/>'
                   + "".join(f'<circle class="dot" style="fill:var({color})" cx="{xi:.1f}" cy="{y(p[key]):.1f}" r="3.5"/>'
                             for xi, p in zip(x, points)))
    lead = points[-1]["predictor_pct"]
    svg.append(f'<text class="end-label" x="{x[-1] - 10:.1f}" y="{y(lead) - 12:.1f}" text-anchor="end">'
               f'predictor, {ks[-1]} parts: {pct(lead)}</text>')
    svg.append(f'<line class="cross" x1="0" x2="0" y1="{top}" y2="{h - bottom}"/>'
               f'<rect class="hit" x="{left}" y="{top}" width="{w - left - right}" height="{h - top - bottom}"/>')
    tips = [f"{p['k']} part{'s' if p['k'] > 1 else ''} on the list: predictor covers {pct(p['predictor_pct'])}\n"
            f"National top parts {pct(p['national_pct'])}, so the predictor adds {p['predictor_pct'] - p['national_pct']:+.1f} points.\n"
            f"Shuffled history (control) {pct(p['shuffled_pct'])}; with the held-back jobs leaked in {pct(p['leaked_pct'])}; "
            f"today's trucks {pct(today)}.\n"
            f"Share of the {jobs:,} held-back jobs whose every needed part is on the list." for p in points]
    data = {"w": w, "xs": x, "ks": ks, "tips": tips}
    keys = "".join(f'<span class="key"{tipattr(n + chr(10) + t)}><i style="background:var({c})"></i>{e(n)}</span>'
                   for _, n, c, t in series)
    return (f'<div class="legend">{keys}</div><div class="scroll"><svg class="curve" viewBox="0 0 {w} {h}" tabindex="0" '
            f'data-curve="{e(json.dumps(data))}" role="img" aria-label="Coverage against carry-list length; the table below '
            f'holds every value">{"".join(svg)}</svg></div>')


def stamp_line(stamp_text, day):
    return (f'<p class="stamp"><span class="build-stamp"{tipattr(stamp_text + chr(10) + "The build that made this page: how many changes the repository held at the change it was built from, the day of that change, and its short code. A + means it was built from changes not yet saved. A fresh clone of the repository builds this same stamp.")}>'
            f'{e(stamp_text)}</span> · results last changed <span class="content-day"{tipattr((day or "unknown") + chr(10) + "The last day any code, query or cached answer that feeds these pages changed. A rebuild that moved nothing keeps it.")}>'
            f'{e(day or "unknown")}</span></p>')


NAV = {"report": ("Instruction manual", "manual.html", "Opens the instruction manual in a new tab: what each figure means, every label, every threshold, and what Staple cannot tell you."),
       "method": ("Methods page", "method.html", "Opens the methods page in a new tab: every formula, every rule of the replays, the prompts, the queries and the checks."),
       "languages": ("Code languages", "languages.html", "Opens the code languages page in a new tab: what Staple is written in, where each part runs, and every program it starts."),
       "back": ("Back to the report", "index.html", "Returns to the report.")}


def shell(kind, title, lede, body, stamp_text, day, chosen=None):
    buttons = {"report": ["report", "method", "languages"], "manual": ["back", "method", "languages"],
               "method": ["back", "report", "languages"], "languages": ["back", "report", "method"]}[kind]
    new_tab = ' target="_blank" rel="noopener"'
    nav = "".join(f'<a class="btn{"" if i == 0 else " ghost"}" href="{NAV[b][1]}"{"" if b == "back" else new_tab}'
                  f'{tipattr(NAV[b][0] + chr(10) + NAV[b][2])}>{e(NAV[b][0])}</a>' for i, b in enumerate(buttons))
    nav += (f'<button type="button" class="btn ghost" data-theme-toggle aria-pressed="false"'
            f'{tipattr("Dark mode" + chr(10) + "The page opens light. This switches it to dark and back, and this browser remembers the choice.")}>Dark mode</button>')
    css, js = (PAGES / "style.css").read_text(encoding="utf-8"), (PAGES / "tips.js").read_text(encoding="utf-8")
    if kind == "report":
        js += "\n" + (PAGES / "plan.js").read_text(encoding="utf-8")
    app = f' class="report" data-view="night" data-chosen="{chosen}"' if kind == "report" else ""
    h1_tip = ("Staple\nPlans a repair fleet's next day on invented history: Claude codes the bookings, Staple routes the "
              "technicians and loads the trucks, and people act on a to-do list before 08:00.")
    madeup = (f"Invented data\n{world.JOBS:,} jobs from seed {world.SEED}, with wrong descriptions, thin model histories and "
              "patternless failures planted on purpose. No real company, customer, part or technician.")
    invented = (f'<p class="inv"><span class="invented"{tipattr(madeup)}>Invented data</span> Every call, technician, part '
                f'and dollar here is invented. The method is real.</p>' if kind == "report" else "")
    out = ' target="_blank" rel="noopener"'
    byline = (f'Designed and directed by <a href="{SITE}"{out}>{AUTHOR}</a>, built with Claude Code. The code is at '
              f'<a href="{REPO}"{out}>{REPO.split("//")[1]}</a>.')
    remembered = 'try{if(localStorage.getItem("staple-theme")==="dark")document.documentElement.dataset.theme="dark"}catch(e){console.warn("Staple: the saved light/dark choice could not be read",e)}'
    url = PAGES_URL + ("" if kind == "report" else FILES[kind])
    share = "".join(f'<meta property="og:{k}" content="{e(str(v))}">' for k, v in (
        ("type", "website"), ("site_name", "Staple"), ("title", title), ("description", lede), ("url", url),
        ("image", f"{PAGES_URL}{CARD}?v={CARD_VERSION}"), ("image:width", CARD_SIZE[0]), ("image:height", CARD_SIZE[1]),
        ("image:alt", "Staple: " + LEDE)))
    head = (f'<meta name="description" content="{e(lede)}">{share}<meta name="twitter:card" content="summary_large_image">'
            '<link rel="icon" href="icon.svg" type="image/svg+xml"><link rel="icon" href="icon-32.png" sizes="32x32">'
            '<link rel="apple-touch-icon" href="apple-touch-icon.png">')
    return (f'<!doctype html>\n<html lang="en" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(title)}</title>{head}<style>{css}</style><script>{remembered}</script></head><body{app}><header class="band"><div class="band-in">'
            f'<h1{tipattr(h1_tip)}>{e("Staple" if kind == "report" else title)}</h1><nav class="actions" aria-label="Pages and display">{nav}</nav>'
            f'<p class="lede">{e(lede)}</p>{invented}{stamp_line(stamp_text, day)}</div></header><main>{body}'
            f'<footer><p>{e(written_in("these pages"))} Nothing is installed beyond Python. Every figure in the evidence comes from the queries in '
            f'{hv("sql/metrics/", "sql/metrics/" + chr(10) + ", ".join(p.stem for p in sorted(metrics.SQL.glob("*.sql"))))}, and the plan is the record the dispatch replay kept.</p>'
            f'<p class="byline">{byline}</p></footer></main><div id="tip" hidden></div>'
            f'<script>{js}</script></body></html>\n')


def notes_in_plain_words():
    """Show a warning as one plain line. Python's own form adds a file path and the source line that raised it."""
    warnings.showwarning = lambda message, *_, **__: print(f"note: {message}", flush=True)


def walked(root=ROOT):
    """The folder's files where there is no git, skipping .git and what .gitignore names: a folder/, a name, *.ext."""
    root = Path(root)
    ignore = [g.strip() for g in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
              if g.strip() and not g.startswith("#")] if (root / ".gitignore").exists() else []

    def skip(rel, folder):
        base = rel.rsplit("/", 1)[-1]
        return base == ".git" or any((folder and g.rstrip("/") in (base, rel)) if g.endswith("/")
                                     else base.endswith(g[1:]) if g.startswith("*.") else g in (base, rel) for g in ignore)
    out = []
    for here, dirs, names in os.walk(root):
        rel = Path(here).relative_to(root).as_posix()
        at = (lambda n: n) if rel == "." else (lambda n: f"{rel}/{n}")
        dirs[:] = [d for d in dirs if not skip(at(d), True)]
        out += [at(n) for n in names if not skip(at(n), False)]
    return sorted(out)


def repo_files(root=ROOT, env=None):
    """Every file git would publish, relative to the repository: the files git holds for the next commit. A scratch
    file not yet added to git is left out, so it changes no count and fails no check. A check that lists another
    folder passes its own environment, because inside a commit hook git's variables point at this repository."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, encoding="utf-8", check=True, env=env).stdout
    except (OSError, subprocess.CalledProcessError) as err:
        why = "git is not installed" if isinstance(err, OSError) else "this folder holds no git history"
        warnings.warn(f"git could not list the files ({why}), so the folder was walked, skipping what .gitignore names")
        return walked(root)
    return sorted(p for p in out.split("\0") if p and (Path(root) / p).is_file())


def kind_of(path):
    """A file's kind: a dotfile's own name, else what follows the last dot."""
    base = path.rsplit("/", 1)[-1]
    if base.startswith(".") and "." not in base[1:]:
        return base[1:].lower()
    return base.rsplit(".", 1)[1].lower() if "." in base[1:] else ""


def languages(files=None):
    """Every file by language or format, read from git's list on each build so no page states a stale count. The
    counts leave out docs/, the published copy the build writes from the rest."""
    files = repo_files() if files is None else files
    source = [p for p in files if not p.startswith(PUBLISHED.name + "/")]
    formats = {}
    for p in source:
        if kind_of(p) in FORMATS:
            formats.setdefault(FORMATS[kind_of(p)], []).append(p)
    return {"code": {lang: [p for p in source if CODE_LANGUAGES.get(kind_of(p)) == lang] for lang in CODE_LANGUAGES.values()},
            "formats": formats, "published": [p for p in files if p not in source],
            "unknown": [p for p in files if kind_of(p) not in CODE_LANGUAGES and kind_of(p) not in FORMATS]}


counted = plan.counted


def written_in(where="the pages"):
    code = languages()["code"]
    n = {lang: len(paths) for lang, paths in code.items()}
    queries = sum(p.startswith("sql/metrics/") for p in code["SQL"])
    return (f"Written in Python, SQL and JavaScript: {n['Python']} Python files invent the history, predict parts, load "
            f"trucks, ask Claude, replay dispatch, write {where} and check it all; {n['SQL']} SQL files hold the tables "
            f"and the {queries} metric queries; {n['JavaScript']} JavaScript files run the hints and switches. Beside "
            f"them, {counted(n['Bash'], 'Bash script')} {'runs' if n['Bash'] == 1 else 'run'} the checks before each "
            f"commit, and {counted(n['HTML'], 'HTML file')} and {counted(n['CSS'], 'CSS file')} lay out the pages.")


def tokens():
    """The light scheme's colors, read from the stylesheet so the icons and the card never hold a second copy."""
    light = re.search(r":root\{([^}]*)\}", (PAGES / "style.css").read_text(encoding="utf-8")).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", light))


def mark_svg(size=32):
    t = tokens()
    bars = "".join(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"/>' for x0, y0, x1, y1 in STAPLE)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="{size}" height="{size}">'
            f'<rect width="32" height="32" rx="7" fill="{t["--band"]}"/><g fill="{t["--cta"]}">{bars}</g></svg>')


def mark_png(size):
    """The mark as an opaque square PNG, four by four samples a pixel. Opaque, because iOS puts a clear icon on black."""
    t = tokens()
    band, cta = (bytes.fromhex(t[k][1:]) for k in ("--band", "--cta"))
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            hit = sum(any(x0 <= (x + (i + .5) / 4) * 32 / size < x1 and y0 <= (y + (j + .5) / 4) * 32 / size < y1
                          for x0, y0, x1, y1 in STAPLE) for i in range(4) for j in range(4)) / 16
            row += bytes(round(b + (c - b) * hit) for b, c in zip(band, cta))
        rows.append(bytes(row))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


ASSETS = ("icon.svg", "icon-32.png", "apple-touch-icon.png", CARD)


def assets():
    """The files published beside the pages: the tab icons, drawn here, and the share card, photographed once."""
    return dict(zip(ASSETS, (mark_svg().encode(), mark_png(32), mark_png(180), (PAGES / CARD).read_bytes())))


def card_html():
    """The share card as a 1200 by 630 page. `python run.py --card` photographs it into staple/pages/card.png."""
    w, h = CARD_SIZE
    root = re.search(r":root\{[^}]*\}", (PAGES / "style.css").read_text(encoding="utf-8")).group(0)
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{root}'
            f'html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden}}*{{box-sizing:border-box}}'
            f'body{{background:var(--band);color:var(--band-ink);font-family:system-ui,"Segoe UI",sans-serif;padding:70px 80px;'
            f'display:flex;flex-direction:column;justify-content:space-between}}.top{{display:flex;align-items:center;gap:30px}}'
            f'h1{{font-size:110px;line-height:1;margin:0;letter-spacing:-2px}}p{{margin:0}}'
            f'.lede{{font-size:42px;line-height:1.28;max-width:1040px;text-wrap:balance}}.steps{{font-size:26px;font-weight:700;color:var(--cta);white-space:nowrap}}'
            f'.foot{{font-size:25px;color:var(--band-2);margin-top:10px}}</style></head><body>'
            f'<div class="top">{mark_svg(116)}<h1>Staple</h1></div><p class="lede">{e(LEDE)}</p>'
            f'<div><p class="steps">Claude codes every booking · Staple plans the trucks · people act before 08:00</p>'
            f'<p class="foot">Every name and figure is invented; the method is real · {PAGES_URL.split("//")[1].rstrip("/")}</p>'
            f'</div></body></html>')


def card_fingerprint():
    return hashlib.sha256(card_html().encode("utf-8")).hexdigest()


def picture_pages(page):
    """The two files the README's picture is photographed from, by name. page.html is the report with all but the
    day's plan hidden, the day as it ran showing and a moved call open. frame.html holds it at the picture's exact
    width, under a strip that says the day is invented."""
    t = tokens()
    w, h = PICTURE_SIZE
    events = re.search(r'class="day-btn"[^>]*aria-pressed="true" aria-label="[^":]*: ([^"]*)"', page).group(1)
    # The photograph is taken at once, so the page it is taken of never glides to its place.
    hide = ("<style>html{scrollbar-width:none;scroll-behavior:auto!important}.band,.split-top,.day-proof,.map-panel,.grid.two,.how,.evidence-head,"
            ".evidence,.pair,#run,footer,#tip{display:none!important}.report main{padding-top:12px}</style>")
    act = ("<script>addEventListener('load', () => {document.querySelector('.vbtn[data-view=ran]').click();"
           "const moved = [...document.querySelectorAll('.board-panel:not([hidden]) .v-ran .blk.moved')];"
           "moved[Math.min(4, moved.length - 1)].click();scrollTo(0, 0)})</script>")
    frame = (f'<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:{t["--page"]};'
             f'font:17px/{PICTURE_STRIP}px system-ui,sans-serif"><div style="height:{PICTURE_STRIP}px;background:{t["--band"]};'
             f'color:{t["--band-ink"]};padding:0 16px;display:flex;gap:14px;align-items:center"><b style="font-size:21px">Staple</b>'
             f'<span>one invented day, as it ran: {e(events)}</span><span style="margin-left:auto;font-size:14px;line-height:22px;'
             f'border:1px solid {t["--band-2"]};border-radius:4px;padding:0 6px">Invented data</span></div>'
             f'<iframe src="page.html" style="width:{w}px;height:{h - PICTURE_STRIP}px;border:0;display:block"></iframe></body></html>')
    at = page.rindex("</body>")
    return {"page.html": page[:at] + hide + act + page[at:], "frame.html": frame}


def picture_fingerprint(page):
    """A fingerprint of what the README's picture shows of a report: its strip, the styles and the week's plan."""
    plan_part = page[page.index('<div class="daybar"'):page.index('<div class="grid two">')]
    styles = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    return hashlib.sha256((picture_pages(page)["frame.html"] + styles + plan_part).encode("utf-8")).hexdigest()


def day_name(day):
    return dt.date.fromisoformat(day).strftime("%a, %b %d").replace(" 0", " ")


def chance_class(c):
    return "c5" if c >= 0.9 else "c4" if c >= 0.8 else "c3" if c >= 0.7 else "c2" if c >= 0.5 else "c1"


def ai_line(c):
    a = c["ai"]
    if a["question"]:
        settled = (f"the customer's answer coded it {c['problem']}" if a["reply"]
                   else f"no answer fit, so it runs on {c['problem']}")
        return f"Claude, {a['first_confidence']:.0%} sure, asked one question; {settled}."
    return f"Claude read {c['problem']}, {a['confidence']:.0%} sure."


def call_tip(c, ctx):
    rate = ctx.crew.belief[(c["tech"], c["type"])][0]
    why = (f"Why {c['tech']}: lowest expected cost; next best {c['runner']}, {cents(c['gap']) + ' more' if round(c['gap'], 2) else 'at the same cost'}."
           if c["runner"] else f"Why {c['tech']}: the only one with room.")
    return "\n".join([
        f"Call {c['job']} · {plan.clock(c['start'])}–{plan.clock(c['end'])} · zone {c['zone'] + 1}"
        + (" · urgent" if c["priority"] == 1 else ""),
        f"{c['type'].capitalize()}: “{c['words']}”", ai_line(c),
        "Parts: " + "; ".join(f"{p['name']} {p['odds']:.0%} {p['where']}" for p in c["parts"]) + ".",
        f"First-time fix chance {c['chance']:.0%} ({c['tech']} fixes {rate:.0%} of {c['type']}s).", why])


def ran_tip(c, moved):
    lines = [f"Call {c['job']} as the day ran · {plan.clock(c['start'])}–{plan.clock(c['end'])} · {c['tech']}",
             f"{c['type'].capitalize()}: {c['problem']}. First-time fix chance {c['chance']:.0%}.",
             "Every needed part was aboard." if c["covered"] else "A needed part was not aboard: a repeat visit."]
    if c["job"] in moved:
        m = moved[c["job"]]
        why = "called out sick" if m["why"] == "sick" else "broke down"
        lines.append(f"Moved from {m['from']}, who {why}. First-time fix chance {m['before']:.0%} on the planned truck, "
                     f"{m['after']:.0%} on this one." if m["before"] is not None else f"Moved from {m['from']}, who {why}.")
    if c["unplanned"]:
        lines.append("Added on the day, so no night plan saw it.")
    if c["changed"]:
        lines.append("Escalated to urgent on the day." if c["changed"] == 1 else "Eased to low priority on the day.")
    return "\n".join(lines)


def at(minutes):
    return f"{100 * minutes / dispatch.DAY:.1f}%"


def board(d, ctx, view):
    m = d["morning"]
    moved = {x["job"]: x for x in m["moved"]}
    ticks = "".join(f'<span style="left:{at(60 * h)}">{8 + h:02d}</span>' for h in range(dispatch.DAY // 60 + 1))
    rows = []
    for tech in ctx.crew.techs:
        info = ctx.techs[tech]
        tip = (f"{tech} · truck {info['truck']}\n{info['level'].capitalize()} {info['profile']}, zone {info['home'] + 1}"
               + (", certified." if info["certified"] else ".") + " Click to light up this truck and open its load sheet.")
        blocks, kind = [], ""
        if view == "night":
            for c in d["trucks"][tech]["calls"]:
                blocks.append(f'<span class="blk {chance_class(c["chance"])}{" ai" if c["ai"]["question"] else ""}" '
                              f'style="left:{at(c["start"])};width:{at(c["end"] - c["start"])}"{tipattr(call_tip(c, ctx))}></span>')
        else:
            for c in d["trucks"][tech]["ran"]:
                marks = "".join(f" {k}" for k, on in (("moved", c["job"] in moved), ("unpl", c["unplanned"]),
                                                      ("miss", not c["covered"]), ("up", c["changed"] == 1)) if on)
                blocks.append(f'<span class="blk {chance_class(c["chance"])}{marks}" style="left:{at(c["start"])};'
                              f'width:{at(c["end"] - c["start"])}"{tipattr(ran_tip(c, moved))}>'
                              f'{"<span class=fr>from " + moved[c["job"]]["from"] + "</span>" if c["job"] in moved else ""}</span>')
            gave = sum(x["from"] == tech for x in m["moved"])
            if tech in m["sick"]:
                kind = " sick"
                sick_tip = (f"{tech} called out sick at {SICK_AT}\nThe night plan and the loads were already made. "
                            + (f"{plan.counted(gave, 'call').capitalize()} planned for {tech} moved to other trucks, which could "
                               f"use only the parts they already carried." if gave else f"{tech} had no calls planned, so none moved."))
                blocks.append(f'<span class="lane-note"{tipattr(sick_tip)}>'
                              f'called out sick at {SICK_AT} · {plan.counted(gave, "call")} moved</span>')
            if tech in m["broken"]:
                kind, when = " broken", m["broken"][tech]
                broke_tip = (f"Truck {info['truck']} broke down at {plan.clock(when)}\n"
                             + (f"Its stock, {plan.counted(m['stranded'][tech], 'part')}, is stranded for the day. "
                                if m["stranded"][tech] else "")
                             + f"{plan.counted(gave, 'later call').capitalize()} moved to other trucks.")
                blocks.append(f'<span class="dead" style="left:{at(when)}"></span><span class="brk" style="--at:{at(when)}"'
                              f'{tipattr(broke_tip)}>'
                              f'&#9888; broke down {plan.clock(when)} · {some(gave)} moved</span>')
        rows.append(f'<div class="row{kind}" data-tech="{tech}"><span class="who"{tipattr(tip)}>{tech}</span>'
                    f'<span class="lane">{"".join(blocks)}</span></div>')
    # One call is in the Tab order; the arrow keys move from it to every other call and technician on the board.
    lanes = "".join(rows).replace('<span class="blk ', '<span tabindex="0" class="blk ', 1)
    served = sum(len(d["trucks"][t]["calls" if view == "night" else "ran"]) for t in ctx.crew.techs)
    label = (f"The board, {'as planned at night' if view == 'night' else 'as the day ran'}: {served} calls on "
             f"{len(ctx.crew.techs)} trucks. The arrow keys move between calls and technicians, and Enter opens one.")
    return (f'<div class="board v-{view}" role="group" aria-label="{e(label)}"><div class="row axis"><span></span>'
            f'<span class="lane ticks" aria-hidden="true">{ticks}</span></div>{lanes}</div>')


def spot(zone, salt, ring=1.0):
    """A stop's place inside its zone's square: spread by the call number, since routes run zone to zone."""
    x, y = plan.ZONES[zone]
    angle, radius = (salt * 2.399963) % 6.2832, (8 + (salt % 4) * 7) * ring
    return 50 + 100 * x + radius * math.cos(angle), 50 + 100 * y + radius * math.sin(angle)


def route_map(d, ctx, centers, shown):
    m = d["morning"]
    moved = {x["job"]: x for x in m["moved"]}
    cells = "".join(f'<rect class="zone" x="{100 * x + 3}" y="{100 * y + 3}" width="94" height="94" rx="8"/>'
                    f'<text class="zone-n" x="{100 * x + 8}" y="{100 * y + 22}">{i + 1}</text>'
                    for i, (x, y) in enumerate(plan.ZONES))
    marks = "".join(f'<rect class="center" x="{100 * plan.ZONES[z][0] + 44}" y="{100 * plan.ZONES[z][1] + 44}" width="12" height="12"/>'
                    for _, z in centers)
    homes = {t: spot(ctx.techs[t]["home"], n * 7 + 3, 1.2) for n, t in enumerate(ctx.crew.techs)}
    layers = {"night": [], "ran": []}
    for tech in ctx.crew.techs:
        hx, hy = homes[tech]
        for view in layers:
            calls = d["trucks"][tech]["calls" if view == "night" else "ran"]
            pts = [spot(c["zone"], c["job"]) for c in calls]
            path = [homes[tech]]
            for x, y in pts:    # a point half way along each leg, where the chosen route draws its arrow
                path += [((path[-1][0] + x) / 2, (path[-1][1] + y) / 2), (x, y)]
            line = " ".join(f"{x:.0f},{y:.0f}" for x, y in path)
            stops = []
            for i, ((x, y), c) in enumerate(zip(pts, calls), 1):
                if view == "night":
                    stop_tip = (f"Stop {i} · {tech} · {plan.clock(c['start'])}\n"
                                f"Call {c['job']}, {c['type']}, {c['chance']:.0%}.")
                    stops.append(f'<circle class="stop{" ai" if c["ai"]["question"] else ""}" cx="{x:.0f}" cy="{y:.0f}" r="4.5"'
                                 f'{tipattr(stop_tip)}/>')
                elif c["job"] in moved:
                    moved_tip = (f"Call {c['job']} moved from {moved[c['job']]['from']} to {tech}\n"
                                 f"Reached at {plan.clock(c['start'])}, with only the parts on {tech}'s truck.")
                    stops.append(f'<line class="hand" x1="{hx:.0f}" y1="{hy:.0f}" x2="{x:.0f}" y2="{y:.0f}"/>'
                                 f'<circle class="stop moved" cx="{x:.0f}" cy="{y:.0f}" r="4.5"'
                                 f'{tipattr(moved_tip)}/>')
            if view == "ran" and tech in m["broken"]:
                x, y = pts[-1] if pts else homes[tech]
                stops.append(f'<text class="brk-map" x="{x + 5:.0f}" y="{y - 4:.0f}">&#9888;</text>')
            layers[view].append(f'<g class="rt" data-tech="{tech}"><polyline points="{line}"/>{"".join(stops)}'
                                f'<circle class="home" cx="{hx:.0f}" cy="{hy:.0f}" r="2.5"/></g>')
    tip = (f"Routes, zone to zone\n{len(world.ZONES)} numbered zones {world.ZONE_MILES:g} miles apart; the squares are the "
           f"{len(world.CENTERS)} distribution centers. Every truck's "
           "route is drawn faint and the chosen one bold, with an arrow on each leg from the dot where it starts. A stop "
           "sits inside its zone; drive times run zone to zone, not on roads.")
    label = f"Map of the {len(world.ZONES)} zones and every truck route; the board beside it holds every call"
    return (f'<section class="panel map-panel" data-day="{d["day"]}"{"" if shown else " hidden"}><h3{tipattr(tip)}>The route map</h3>'
            f'<div class="scroll"><svg class="map" viewBox="0 0 400 300" role="img" aria-label="{e(label)}"{tipattr(tip)}>'
            f'{cells}{marks}<g class="v-night">{"".join(layers["night"])}</g><g class="v-ran">{"".join(layers["ran"])}</g></svg></div></section>')


def sheet_html(d, tech, ctx, shown):
    s, info, calls = d["trucks"][tech]["sheet"], ctx.techs[tech], d["trucks"][tech]["calls"]
    rows = s["rows"]
    moves = [r for r in rows if r["status"] != "keep"]
    keep = [r for r in rows if r["status"] == "keep"]

    def why(r):
        if r["status"] == "take off":
            return "no call needs it" if r["worth"] <= 0 else "outranked for space"
        reasons = [f"call {j} {p:.0%}" for j, p in r["calls"]]
        if r["spare"] >= 0.5:
            reasons.append(f"spare for unplanned calls {cents(r['spare'])}")
        return ", ".join(reasons) or "spare for unplanned calls"
    head = "".join(f"<th{tipattr(t + chr(10) + h)}>{e(t)}</th>" for t, h in (
        ("Do", "Tonight, at the center."), ("Part", "With its made-up cost."), ("Worth", "Dollars, tomorrow."),
        ("Why", "The calls behind it.")))
    body = "".join(f'<tr class="{r["status"].replace(" ", "-")}"><td>{e(r["status"])}</td><td>{e(r["name"])} ({dollars(r["cost"])})</td>'
                   f'<td>{cents(r["worth"])}</td><td>{e(why(r))}</td></tr>' for r in moves)
    needed = [r["name"] for r in keep if r["calls"]]
    spare = Counter(ctx.kind[r["part"]] for r in keep if not r["calls"])
    aboard = (f"{len(keep)} parts. For tomorrow's calls: {e(', '.join(needed)) or 'none'}. Stock for unplanned calls: "
              + (", ".join(f"{e(k)} {n}" for k, n in sorted(spare.items())) or "none"))
    on, off = (sum(r["status"] == status for r in rows) for status in ("put on", "take off"))
    title = f"Load sheet · truck {info['truck']}: put on {on}, take off {off}, keep {len(keep)}"
    tip = (f"{title}\nWhat the parts room does to {tech}'s truck tonight, with each part's worth and the booked calls "
           "behind it. Click to open or close the list.")
    table = (f'<div class="scroll"><table class="load"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
             if moves else "<p>No change to the load tonight.</p>")
    return (f'<details class="sheet" data-sheet="{tech}"{"" if shown else " hidden"}><summary{tipattr(tip)}>{e(title)}</summary>'
            f'<p class="sub">{s["space"]} of {s["capacity"]} space units · {dollars(s["value"])} of stock aboard · '
            f'{plan.counted(len(calls), "call")} tomorrow</p>'
            f'{table}<p class="keep"><strong>Keep aboard:</strong> {aboard}.</p></details>')


TODO_PROOF = {"Parts room": "inventory_roi", "Call center": "ai_step", "Dispatch": "dispatch_scorecard", "Buyer": "buyer_scorecard"}


def ai_panel(d, ctx, shown):
    a = d["ai"]
    cards = []
    for c in a["calls"]:
        ai = c["ai"]
        first = ctx.labels[f"{c['type']}.{ai['first']}"]
        pick = ' class="pick"'
        opts = "".join(f'<li{pick if o["code"] == ai["reply"] else ""}>{e(o["text"])}</li>' for o in ai["offered"])
        then = f"Coded {c['problem']}" if ai["reply"] else "Still unsure: the call center rings the customer"
        where = f"{c['tech']} at {plan.clock(c['start'])}" if c.get("tech") else "no technician had room"
        now = ", ".join(p["name"] for p in c["parts"])
        before = ", ".join(c["first_parts"])
        tip = (f"Call {c['job']}: Claude at booking\nThe customer said \"{c['words']}\". Claude's first reading was {first} at "
               f"{ai['first_confidence']:.0%}, below the {triage.BLEND_BELOW:.0%} it needs, so it asked one question. "
               + (f"The answer coded the call {c['problem']}." if ai["reply"]
                  else f"No offered answer fit, so the call runs on {c['problem']} and goes to the call center."))
        parts = (f'<p class="parts"><span>First guess would load</span> {e(before)}<br><span>Now loads</span> {e(now)}</p>'
                 if now and before and now != before else "")
        cards.append(f'<div class="ask" data-call="{c["job"]}"{" data-tech=" + chr(34) + c["tech"] + chr(34) if c.get("tech") else ""}{tipattr(tip, focus=True)}>'
                     f'<p class="said">{e(c["type"].capitalize())}: &ldquo;{e(c["words"])}&rdquo;</p><p>Claude read <strong>{e(first)}</strong> '
                     f'<span class="conf"><i style="width:{100 * ai["first_confidence"]:.0f}%"></i></span> {ai["first_confidence"]:.0%}</p>'
                     f'<p class="asked">It asked: &ldquo;{e(ai["question"])}&rdquo;</p>'
                     f'<ul class="opts">{opts}</ul><p class="then">{e(then)} · {e(where)}</p>{parts}</div>')
    left = a["asked"] - a["settled"]
    tip = (f"AI at booking\n{triage.MODEL_NAME} ({triage.MODEL}) read every booking's own words and picked the problem code "
           f"this plan runs on. Below {triage.BLEND_BELOW:.0%} sure, it asked the customer one question. Its answers were "
           "made once by the model and saved with the code, so this page calls no model and holds no key.")
    return (f'<section class="panel ai-panel" data-day="{d["day"]}"{"" if shown else " hidden"}><h3{tipattr(tip)}>AI at booking: '
            f'Claude coded all {a["coded"]} bookings{q("follow-up question")}</h3>'
            f'<p class="sub">{a["sure"]} read {triage.BLEND_BELOW:.0%} sure or more were coded at once. '
            + (f'{a["asked"]} {"was" if a["asked"] == 1 else "were"} unsure, so Claude asked each customer one question: '
               f'{plan.counted(a["settled"], "answer")} settled the code, and {some(left)} {"goes" if left == 1 else "go"} to '
               f'the call center. Each unsure call is below, and wears a marked corner on the night plan\'s board. Click one '
               f'to open it there.</p>' if a["asked"] else
               'None were unsure, so Claude asked no customer a question.</p>')
            + f'<div class="asks">{"".join(cards)}</div></section>')


def todo_panel(d, shown, titles):
    """The day's to-do list by owner, in a box that scrolls inside itself. An item opens the truck or the call it names.
    titles holds each evidence card's heading, so an owner's link names the card it opens."""
    groups = []
    for owner, proof in TODO_PROOF.items():
        items = [i for i in d["todo"] if i["owner"] == owner]
        if not items:
            continue
        tag = '<b class="ai-tag">AI</b>'
        lis = "".join(f'<li class="todo-item"{" data-tech=" + chr(34) + i["tech"] + chr(34) if i["tech"] else ""}'
                      f'{" data-call=" + chr(34) + str(i["job"]) + chr(34) if i["job"] else ""}'
                      f'{tipattr(i["text"] + chr(10) + i["detail"], focus=True)}>'
                      f'{tag if owner == "Call center" or i.get("ai") else ""}{e(i["text"])}</li>' for i in items)
        why = f"Why these items\nOpens the evidence card behind this kind of action: {titles[proof]}."
        who = next((i["said"]["who"] for i in items if i.get("said") and i["said"]["who"]), None)
        groups.append(f'<div class="todo-group"><h3>{e(owner)} · {e(items[0]["when"])} · {len(items)} '
                      f'{"· decided by " + e(who) + " " if who else ""}'
                      f'<a class="why" href="#proof-{proof}" data-proof="{proof}"{tipattr(why)}>evidence</a></h3><ul>{lis}</ul></div>')
    tip = ("To do before 08:00\nWhat people act on, by owner. Click an item to light up its truck and open the call or the "
           "load sheet it names in the plan below. Each owner's evidence link opens the card behind that kind of action.")
    return (f'<section class="panel todo-panel" data-day="{d["day"]}"{"" if shown else " hidden"}><h2{tipattr(tip)}>To do before 08:00'
            f'<span class="count">{plan.counted(len(d["todo"]), "item")}</span></h2><div class="todo box">{"".join(groups)}</div></section>')


def morning_panel(d, ctx, shown):
    m = d["morning"]
    items = []

    def moves(tech, chance):
        return "".join(f'<li>call {x["job"]} &rarr; {x["to"] or "rolled to tomorrow"}'
                       + (f', first-time fix chance {x["before"]:.0%} &rarr; {x["after"]:.0%}' if chance and x["to"] and x["before"] is not None else "")
                       + "</li>" for x in m["moved"] if x["from"] == tech)
    for tech in m["sick"]:
        n = sum(x["from"] == tech for x in m["moved"])
        items.append(f'<li class="evt" data-tech="{tech}" data-view="ran"{tipattr(f"{tech} called out sick at {SICK_AT}" + chr(10) + "The night plan and the loads were already made, so each call moved by the same rule to a truck that could take it, using only the parts that truck carried. Click to see it on the board.", focus=True)}>'
                     f'<strong>{tech} called out sick at {SICK_AT}</strong>: {plan.counted(n, "call")} moved<ul>{moves(tech, True)}</ul></li>')
    for tech, when in m["broken"].items():
        n = sum(x["from"] == tech for x in m["moved"])
        broke_tip = (f"Truck {ctx.techs[tech]['truck']} broke down at {plan.clock(when)}\n"
                     f"{plan.counted(m['stranded'][tech], 'part').capitalize()} stranded with it. The calls it had not reached moved to other trucks. "
                     "Click to see it on the board.")
        items.append(f'<li class="evt" data-tech="{tech}" data-view="ran"{tipattr(broke_tip, focus=True)}>'
                     f'<strong>Truck {ctx.techs[tech]["truck"]} ({tech}) broke down at {plan.clock(when)}</strong>: '
                     f'{plan.counted(m["stranded"][tech], "part")} stranded, {plan.counted(n, "call")} moved<ul>{moves(tech, True)}</ul></li>')
    if not m["sick"] and not m["broken"]:
        items.append("<li>No sick calls and no breakdowns on this day.</li>")
    up = sum(p == 1 for _, p in m["changed"].values())
    items.append(f'<li>{plan.counted(len(m["changed"]), "call").capitalize()} changed priority on the day'
                 + (f', {up if up else "none"} of them escalated to urgent (&#9650; on the board)' if m["changed"] else "") + ".</li>")
    tip = (f"{SICK_AT} and after\nWhat changed after the plan was made: sick calls at {SICK_AT}, breakdowns during the day and "
           "priority changes. Switch the board to As the day ran to see each call move.")
    return (f'<section class="panel changed-panel" data-day="{d["day"]}"{"" if shown else " hidden"}><h3{tipattr(tip)}>{SICK_AT} and '
            f'after: what changed</h3><ul class="morning">{"".join(items)}</ul></section>')


def day_line(d, added, shown):
    """The day in one line under the day buttons: its calls, how many were booked the night before and how many came in
    on the day, then the plan against the current rules. added is the count of calls that came in on the day."""
    o, b = d["ours"], d["base"]
    calls, rolled, theirs = int(o["calls"]), int(o["rolled"]), int(b["rolled"])
    saved = gap(b["served"] - b["fixes"], o["served"] - o["fixes"], 1)

    def fact(value, words, tip):
        return f'<span class="fact"{tipattr(tip)}><b>{e(value)}</b>{"" if words.startswith(",") else " "}{e(words)}</span>'
    facts = [
        fact(plan.counted(calls, "call"), f"on the day: {calls - added} booked the night before, {added} added on the day",
             f"{plan.counted(calls, 'call')} on {day_name(d['day'])}\nAll invented. The night plan saw the {calls - added} booked by "
             f"then; the other {added} came in during the day, after the plan was made. "
             + (f"{rolled} could not fit any route and {'rolls' if rolled == 1 else 'roll'} to tomorrow" if rolled else
                "Every one fits a route")
             + f"; under the current rules {plan.counted(theirs, 'call')} {'rolls' if theirs == 1 else 'roll'}."),
        fact(plan.counted(len(d["trucks"]), "truck"), f", with {plan.counted(rolled, 'call') if rolled else 'no call'} rolled to tomorrow",
             f"{plan.counted(len(d['trucks']), 'truck')}, one to a technician\n{GLOSSARY['rolled call']}"),
        fact(f"{100 * o['fixes'] / o['calls']:.1f}%", f"fixed on the first visit, against {100 * b['fixes'] / b['calls']:.1f}% under the current rules",
             f"{o['fixes']:.0f} of {calls} calls expected fixed first time\nThe current rules, the nearest technician on "
             f"today's fixed stock: {b['fixes']:.0f}. Same calls, same sick calls and breakdowns, both on Claude's codes."),
        fact(f"{saved:.1f}", f"repeat visits avoided, at a made-up {dollars(load.REPEAT_VISIT)} each",
             f"Expected: {fewer(saved)}\nCost a call {cents(o['cost'] / o['served'])} "
             f"against {cents(b['cost'] / b['served'])}."),
        fact(f"{d['moves']}", "parts moved tonight",
             f"{d['moves']} parts put on or taken off overnight across the fleet\nA truck keeps a part only while it is worth "
             "more than its space, its holding cost and a move.")]
    return f'<p class="dayline" data-day="{d["day"]}"{"" if shown else " hidden"}>{" · ".join(facts)}</p>'


def board_panel(d, ctx, shown):
    """One day's board, as planned at night and as the day ran, with the legend for the view that shows."""
    def key(name, mark=""):
        return f'<span><span class="sw {mark}"></span>{name}</span>'
    legend = ('<p class="legend-line"' + tipattr("Reading the board\nEach block is a call, from its start to its end. Its shade is "
              "the plan's first-time fix chance. Marks show what Claude asked, what moved, what was added and what went wrong.")
              + '><span>First-time fix chance:</span>' + key("under 50%", "c1") + key("50%", "c2") + key("70%", "c3") + key("80%", "c4")
              + key("90%+", "c5") + f'<span class="v-night">{key("Claude asked", "c2 ai")}</span><span class="v-ran">'
              + key("moved", "c2 moved") + key("added on the day", "c2 unpl") + key("part missing", "c2 miss")
              + key("escalated to urgent", "c2 up") + '</span></p>')
    calls, ran = (sum(len(t[k]) for t in d["trucks"].values()) for k in ("calls", "ran"))
    count = (f'<span class="count v-night">{calls} calls on {len(d["trucks"])} trucks</span>'
             f'<span class="count v-ran">{ran} calls on {len(d["trucks"])} trucks</span>')
    tip = ("The board\nEach row is a technician from " + plan.clock(0) + " to " + plan.clock(dispatch.DAY) + " and each block "
           "a call. Click a call to open it beside the board. Click a technician to light up that truck and open its load sheet.")
    return (f'<section class="panel board-panel" data-day="{d["day"]}"{"" if shown else " hidden"}><h3{tipattr(tip)}>The board{count}</h3>'
            f'{board(d, ctx, "night")}{board(d, ctx, "ran")}{legend}</section>')


def day_proof(d, shown):
    o, b = d["ours"], d["base"]

    def diff(x, y, n, fmt):
        """The difference of the two figures as shown, so the row adds up on the page."""
        d = gap(x, y, n)
        if not d:
            return "no difference"
        return ("+" if d > 0 else "−") + (f"{abs(d):.1f} points" if fmt is share else fmt(abs(d)))

    def share(v):
        return f"{v:.1f}%"
    rows = [("Fixed on the first visit", 100 * o["fixes"] / o["calls"], 100 * b["fixes"] / b["calls"], 1, share),
            ("Repeat visits expected", o["served"] - o["fixes"], b["served"] - b["fixes"], 1, lambda v: f"{v:.1f}"),
            ("Drive minutes a call", o["drive"] / o["served"], b["drive"] / b["served"], 1, lambda v: f"{v:.1f}"),
            ("Cost a call", o["cost"] / o["served"], b["cost"] / b["served"], 2, cents),
            ("Calls rolled to tomorrow", o["rolled"], b["rolled"], 0, lambda v: f"{v:.0f}")]
    head = "".join(f"<th{tipattr(t + chr(10) + h)}>{e(t)}</th>" for t, h in (
        ("Measure", "What is compared."), ("Staple", "This plan: Claude's codes, the skills-and-parts dispatch rule and balanced loads."),
        ("Current rules", "The nearest qualified technician on today's fixed truck stock, with the same calls and surprises."),
        ("Difference", "Staple against the current rules.")))
    body = "".join(f"<tr><td>{e(name)}</td><td>{f(x)}</td><td>{f(y)}</td><td>{diff(x, y, n, f)}</td></tr>"
                   for name, x, y, n, f in rows)
    saved = gap(b["served"] - b["fixes"], o["served"] - o["fixes"], 1)
    tip = (f"{day_name(d['day'])}, against the current rules\nBoth replays of this day run on Claude's codes, with the same sick "
           "calls, breakdowns and calls added on the day.")
    return (f'<section class="panel day-proof" data-day="{d["day"]}"{"" if shown else " hidden"}><h3{tipattr(tip)}>'
            f'{e(day_name(d["day"]))}, against the current rules</h3><p class="sub">Both on Claude\'s codes, with the same surprises.</p>'
            f'<div class="scroll"><table class="cmp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
            f'<p class="do"><strong>What it means:</strong> on this invented day the plan expects {fewer(saved)}. '
            f'That is how the method does on invented data, not a result: the same comparison on real history is the first '
            f'thing to run.</p></section>')


def tech_card(tech, ctx, shown):
    info = ctx.techs[tech]
    skills = sorted((t, r, s) for (x, t), (r, s) in ctx.crew.belief.items() if x == tech)
    head = "".join(f"<th{tipattr(t + chr(10) + h)}>{e(t)}</th>" for t, h in (
        ("Appliance", "An appliance this technician can repair."), ("Fixes first time", "Their success rate with the parts aboard, learned from the training history."),
        ("Speed", "Their repair time against an average technician's; below 1 is faster. It is given to the rule, not "
                  "learned, because the invented history records no repair minutes.")))
    body = "".join(f"<tr><td>{e(t)}</td><td>{r:.0%}</td><td>{s:.2f}</td></tr>" for t, r, s in skills)
    tip = f"{tech}\nThe chosen technician: what the dispatch rule knows about them. Choose another on the board or the map."
    return (f'<div class="tech-card" data-tech-card="{tech}"{"" if shown else " hidden"}><h3{tipattr(tip)}>'
            f'{tech} · {e(info["level"])} {e(info["profile"])}{", certified" if info["certified"] else ""}, truck {info["truck"]}</h3>'
            f'<table class="cmp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'<p class="sub">Each call goes to the technician with the lowest expected cost, so a specialist fast and sure '
            f'on one appliance draws those calls even from farther away.</p></div>')


def detail_panel(week, ctx, chosen):
    """What a click opens, beside the board: the call, then the chosen technician and that truck's load sheet."""
    cards = "".join(tech_card(t, ctx, t == chosen) for t in ctx.crew.techs)
    sheets = "".join(f'<div class="sheets" data-day="{d["day"]}"{"" if d["day"] == week["open"] else " hidden"}>'
                     + "".join(sheet_html(d, t, ctx, t == chosen) for t in ctx.crew.techs) + "</div>" for d in week["days"])
    return ('<section class="detail" id="detail" aria-label="The opened call, its technician and the truck\'s load sheet">'
            '<div class="call-open" aria-live="polite"><p class="empty">No call is open. Click a call on the board and '
            f'it opens here.</p></div>{cards}{sheets}</section>')


def build_report(results, week, stamp_text, day):
    names = [n for n, *_ in dispatch.POLICIES]
    disp = {r["policy"]: r for r in rows(results, "dispatch_scorecard")}
    now, loaded, skills, parts_only, ours = (disp[n] for n in names)
    shuffled_skills, shuffled_odds = disp[dispatch.SHUFFLED_SKILLS], disp[dispatch.SHUFFLED_ODDS]
    calls = ours["calls"]
    best = {}
    for r in rows(results, "inventory_roi"):
        if r["policy"] not in best or r["cost_a_truck_day"] < best[r["policy"]]["cost_a_truck_day"]:
            best[r["policy"]] = r
    fixed, bal = best[load.TODAY], best[load.BALANCED]
    shuffled_load, equal = best[load.CONTROL_BALANCED], best[load.EQUAL_SPACE]
    priced = [(m, best[load.at_move(m)]) for m in load.MOVE_STEPS]
    controls = {r["control"]: r for r in rows(results, "controls")}
    for name, verdict in WORDS_REST_ON.items():
        if controls[name]["verdict"] != verdict:
            raise ValueError(f"the report's words say the control '{name}' {verdict}, and the controls query says it "
                             f"{controls[name]['verdict']}: rewrite the card before publishing")
    held = [r for r in controls.values() if r["verdict"] != "falls back"]
    ai = {r["method"]: r for r in rows(results, "ai_step")}
    claude, keywords = ai["Claude"], ai[triage.KEYWORDS]
    bands = rows(results, "ai_calibration")
    if not {r["staple_then"] for r in bands} <= {"runs on the code", "asks its question"}:
        raise ValueError("the confidence query names a step the report's words do not know: rewrite the card")
    surest, least = bands[0], bands[-1]
    runs = [r for r in bands if r["staple_then"] == "runs on the code"]
    spare = [r for r in bands if r not in runs and runs and r["agrees_booked_pct"] >= min(x["agrees_booked_pct"] for x in runs)]
    cautious = "".join(f" Where it states {r['stated_confidence']} it matches on {pct(r['agrees_booked_pct'])} of "
                       f"{r['jobs']:,} bookings, as often as the surer bands, and Staple still asks its question there."
                       for r in spare)
    if spare:
        cautious += (f" Staple asks below {triage.BLEND_BELOW}, so that line is cautious on this data. It is a stated choice "
                     "and it is not moved to fit, because it would then be tuned on the jobs it is scored on.")
    said = f"{claude['phrasings']} distinct descriptions across {claude['jobs']:,} held-back bookings"
    shuffled_ai = next(r for m, r in ai.items() if m.startswith("Claude answers shuffled"))
    asked, random_replies = ai[triage.ASKED], ai[triage.RANDOM_REPLIES]
    booked = ai["booked code"]
    causes = rows(results, "parts_caused_reschedules")
    fill = {r["appliance"]: r for r in rows(results, "truck_fill_rate")}
    repeats, caused, all_jobs = (sum(r[c] for r in causes) for c in ("repeat_visits", "parts_caused", "jobs"))
    worst = causes[0]
    cycle = rows(results, "parts_cycle_time")    # the history as recorded first, then each way of handing out calls
    past, wait_now, wait_ours = cycle[0], *(next(r for r in cycle if r["policy"] == n) for n in (names[0], names[-1]))
    if not (wait_ours["repairs_waiting_pct"] < wait_now["repairs_waiting_pct"]
            and wait_ours["days_a_repair"] < wait_now["days_a_repair"]
            and wait_ours["days_when_waiting"] > wait_now["days_when_waiting"]
            and wait_ours["waits_on_backorder_pct"] > wait_now["waits_on_backorder_pct"]):
        raise ValueError("the cycle-time card says Staple leaves fewer repairs waiting, each for longer, with more of "
                         "them on a back-ordered part, and the query no longer says so: rewrite the card before publishing")
    # The rows that wait less than Staple. The card explains them one way, so a row it cannot explain stops the build.
    less = [r for r in cycle[1:] if r["days_a_repair"] < wait_ours["days_a_repair"]]
    if any(r["policy"] not in (parts_only["policy"], shuffled_skills["policy"])
           or disp[r["policy"]]["first_time_fix_pct"] >= ours["first_time_fix_pct"] for r in less):
        raise ValueError("the cycle-time card says a row waits less than Staple only where it does not know who is good "
                         "at what, and fixes fewer calls for it, and the query now shows another: rewrite the card")
    fixes = [f"{r['policy']} {'fixes ' * (i == 0)}{pct(disp[r['policy']]['first_time_fix_pct'])}{' of calls on the first visit' * (i == 0)}"
             for i, r in enumerate(less)]
    wait_less = ("" if not less else
                 f" Of the other rows, {len(less)} {'waits' if len(less) == 1 else 'wait'} less than Staple without knowing "
                 f"who is good at what: {' and '.join(fixes)}, against Staple's {pct(ours['first_time_fix_pct'])}.")
    points = rows(results, "carry_list_curve")
    three = next(p for p in points if p["k"] == load.SHOWN_K)
    stuck = rows(results, "backorder_exposure")
    R = load.REPEAT_VISIT
    ctx, open_day = week["ctx"], week["open"]
    od = next(d for d in week["days"] if d["day"] == open_day)
    receivers = Counter(x["to"] for x in od["morning"]["moved"] if x["to"])
    chosen = receivers.most_common(1)[0][0] if receivers else next(i["tech"] for i in od["todo"] if i["tech"])

    cause_bars = bars([(r["appliance"], r["parts_caused_per_100_jobs"], f"{r['parts_caused_per_100_jobs']:.1f} per 100 jobs",
                        f"{r['parts_caused_per_100_jobs']:.1f} repeat visits per 100 {r['appliance']} jobs for a missing part\n"
                        f"{r['parts_caused']:,} of {r['jobs']:,} jobs; {r['parts_share_of_repeats_pct']:.1f}% of its "
                        f"{r['repeat_visits']:,} repeat visits.\nTruck fill: {pct(fill[r['appliance']]['line_fill_pct'])} of needed "
                        f"part lines aboard, {pct(fill[r['appliance']]['job_fill_pct'])} of jobs with every line aboard "
                        "(truck_fill_rate.sql).", "lead" if r is worst else "") for r in causes])
    cycle_bars = bars([(r["policy"], r["days_a_repair"], f"{r['days_a_repair']:.2f} days · {pct(r['repairs_waiting_pct'])} wait",
                        f"{r['policy']}: {r['days_a_repair']:.2f} days a repair\n{pct(r['repairs_waiting_pct'])} of "
                        f"{r['repairs']:,} repairs wait, {r['days_when_waiting']:.2f} days each.\n"
                        f"{pct(r['waits_on_backorder_pct'])} of those waits are on a part no center could ship.",
                        "lead" if r is wait_ours else "control" if "control" in r["policy"] else "") for r in cycle])
    load_bars = bars([(r["policy"] + (f", {r['parts_per_job']} per job" if r["policy"] in (load.TOP_UP, load.LISTS_ONLY) else ""),
                       r["cost_a_truck_day"], f"{cents(r['cost_a_truck_day'])} · covers {pct(r['covered_pct'])}",
                       f"{cents(r['cost_a_truck_day'])} a truck-day, covering {pct(r['covered_pct'])} of jobs\n"
                       f"Repeat visits ${r['repeat_cost']:.2f} + parts moved ${r['handling_cost']:.2f} + holding ${r['holding_cost']:.2f}.\n"
                       f"{r['parts_aboard']:.0f} parts aboard ({some(r['permanent_parts'])} permanent), {r['moves_a_night']} moved a night, "
                       f"${r['stock_value']:,.0f} of stock; ROI {r['roi']:.0f} dollars a year per dollar held.",
                       "lead" if r is bal else "control" if "shuffled" in r["policy"] else "")
                      for r in sorted(best.values(), key=lambda r: r["cost_a_truck_day"])])
    control_bars = bars([(r["control"], max(r["gain_kept_pct"], 0.0),
                          f"{kept(r)} of the gain · {r['verdict']}",
                          f"{r['control']}: {r['verdict']}\nIt tests {r['gain_tested']}.\n{r['measure'].capitalize()}: "
                          f"{r['without_it']} without it, {r['with_it']} with it, {r['with_control']} with the control.\n"
                          + (f"The control keeps {r['gain_kept_pct']:.0f}% of the gain." if r["gain_kept_pct"] > 0 else
                             "The control lands at or below where the method started, so it keeps none of the gain."),
                          "" if r["verdict"] == "falls back" else "lead") for r in controls.values()])
    def against(r):
        """One policy against the current rules, in points and cents, with a tie said in words."""
        if r["policy"] == now["policy"]:
            return ""
        points = gap(r["first_time_fix_pct"], now["first_time_fix_pct"], 1)
        money = gap(r["cost_per_call"], now["cost_per_call"], 2)
        return ("\nAgainst the nearest technician on today's stock: "
                + (f"{'+' if points > 0 else '−'}{abs(points):.1f} points" if points else "the same share fixed") + ", "
                + (f"{'+' if money > 0 else '−'}{cents(abs(money))} a call" if money else "the same cost a call") + ".")

    dispatch_bars = bars([(r["policy"], r["first_time_fix_pct"], f"{pct(r['first_time_fix_pct'])} · {cents(r['cost_per_call'])} a call",
                           f"{pct(r['first_time_fix_pct'])} fixed on the first visit, {cents(r['cost_per_call'])} a call\n"
                           f"Drive {r['drive_minutes']} minutes a call; a part missing on {pct(r['parts_missing_pct'])} of visits; "
                           f"{some(r['rolled'])} of {r['calls']:,} calls rolled.\n{r['moved']} calls moved by sick days and breakdowns, "
                           f"fixed first time {pct(r['moved_fix_pct'])}."
                           + against(r),
                           "lead" if r is ours else "control" if "control" in r["policy"] else "") for r in disp.values()])
    ai_bars = bars([(r["method"], r["coverage_pct"], f"list covers {pct(r['coverage_pct'])} · agrees {pct(r['agrees_booked_pct'])}",
                     f"The 3-part list built from this reading covers {pct(r['coverage_pct'])} of {r['jobs']:,} jobs\n"
                     f"{pct(r['agrees_booked_pct'])} of bookings match the booked code; {pct(r['agrees_true_pct'])} match the true "
                     "problem, known only because the data is invented.",
                     "lead" if r is claude else "control" if "control" in r["method"] else "")
                    for r in (claude, asked, random_replies, booked, keywords, shuffled_ai)])
    calibration_bars = bars([(f"stated {r['stated_confidence']}", r["agrees_booked_pct"],
                              f"right {pct(r['agrees_booked_pct'])} · {r['staple_then']}",
                              f"Claude said it was {r['stated_confidence']} sure: its code matched the booked code on "
                              f"{pct(r['agrees_booked_pct'])}\n{r['jobs']:,} bookings in {r['phrasings']} distinct phrasings. "
                              f"Against the true problem: {pct(r['agrees_true_pct'])}.\nAt this confidence Staple {r['staple_then']}.",
                              "") for r in rows(results, "ai_calibration")])

    def stuck_tip(r):
        return (f"{pretty_day(r['day'])}, {r['part']}: ships in {plan.days(r['ships_in_days'])}\n"
                f"{counted(r['trucks_holding'], 'truck')} {'stocks' if r['trucks_holding'] == 1 else 'stock'} one; "
                + (f"{counted(r['open_zones'], 'zone')} with no holder within {load.NEARBY_MINUTES} minutes." if r["open_zones"]
                   else f"every zone has a holder within {load.NEARBY_MINUTES} minutes.")
                + f"\nForecast {r['expected_calls']} calls, {r['expected_stuck']} with no "
                f"truck nearby: ${r['dollars_at_risk']:,.0f} at risk.\nBefore it shipped, the held-back days brought {plan.counted(r['actual_calls'], 'call')}"
                + (f", {r['actual_stuck'] if r['actual_stuck'] else 'none'} stuck." if r["actual_calls"] else "."))

    scored = [r for r in stuck if r["back_test"]]    # each back-order once, where every call that came is counted
    flag = rows(results, "backorder_flag")[0]
    exp_calls, exp_stuck = sum(r["expected_calls"] for r in scored), sum(r["expected_stuck"] for r in scored)
    act_calls, act_stuck = sum(r["actual_calls"] for r in scored), sum(r["actual_stuck"] for r in scored)
    this_week = {d["day"] for d in week["days"]}
    stuck_week = [r for r in results["backorder_exposure"][1] if r[0] in this_week]

    # The buyer's card. Its words rest on the fixed rows, so a build whose query no longer supports them stops.
    bought = {r["decider"]: r for r in rows(results, "buyer_scorecard")}
    engine, borrowed = bought[buyer.ARITHMETIC], bought[buyer.CONTROL]
    sends, rushes = (bought[name] for name, _ in buyer.RULES[1:])
    claudes = [(m, bought[triage.named(m)]) for m in agent.MODELS if triage.named(m) in bought]
    if (engine["dollars_lost"] or engine["chose_cheapest"] != engine["decisions"]
            or not all(r["dollars_lost"] > 0 for r in (sends, rushes, borrowed))):
        raise ValueError("the buyer's card says the engine's arithmetic loses nothing and that each fixed rule and the "
                         "control lose money, and the query no longer says so: rewrite the card before publishing")

    def lost_tip(r, more=""):
        return (f"{r['decider']}: ${r['dollars_lost']:,.0f} lost over {r['decisions']:,} decisions\n"
                f"{cents(r['lost_a_decision'])} a decision. {r['chose_cheapest']:,} of its choices cost what the cheapest "
                f"action costs, and {some(r['could_not_be_done'])} could not be carried out.\nIts choices cost "
                f"${r['cost_of_choices']:,.0f} in all, against ${engine['cost_of_choices']:,.0f} for the cheapest." + more)

    def model_point(model, r):
        """One model's row of the buyer's scorecard as a short line, in words that hold for a tie and for a loss."""
        wrong = r["decisions"] - r["chose_cheapest"] - r["could_not_be_done"]
        explained = r["one_misreading_explains"]
        slip = hv("one misreading", "One misreading of the expedite rule\nThe instructions charge expediting the calls that "
                  "come before the early ship day. Each of these choices is the cheapest once it is charged the calls that "
                  "come after it.")
        return join(
            Raw(f"<strong>{e(r['decider'])}</strong> "),
            "lost nothing" if not r["dollars_lost"] else Raw("lost " + hv(f"${r['dollars_lost']:,.0f}", lost_tip(r))),
            (Raw(f" on {wrong} miss{'es' if wrong != 1 else ''}"
                 + (f", all from {slip} of when expediting is charged" if explained == wrong else
                    f", {explained} of them from {slip} of when expediting is charged" if explained else "")) if wrong else ""),
            ". ",
            (f"{r['could_not_be_done']:,} of its choices could not be carried out or were left undecided, and each was "
             "charged as sending anyway. " if r["could_not_be_done"] else ""),
            ("Every note it drafted names the part, its ship day and both trucks of a move" if not r["notes_short"] else
             Raw(hv(f"{r['notes_short']} of its notes", f"{r['notes_short']} of {r['decisions']:,} notes leave a fact out\nThe "
                    "instructions ask each note to name the part and its ship day, and for a move both trucks. The buyer's "
                    "list shows each note as saved and says what it lacks.") + " leave a fact out")),
            (Raw(", and " + hv(f"{r['replies_run_on']} of its replies", f"{r['replies_run_on']} of {r['decisions']:,} replies "
                               "run on\nThe reason or the note runs on into markup left over from the reply's own form. The "
                               "buyer's list cuts it off there and says so.") + " run on into stray markup")
             if r["replies_run_on"] else ""),
            f". Asking for all {r['decisions']:,} cost ",
            hv(cents(r["asking_dollars"]), f"{cents(r['asking_dollars'])} for {r['decisions']:,} decisions\n"
               f"{r['asking_cents_a_decision']:.2f} cents a decision, from the token counts saved with each one, at the "
               f"prices listed on {pretty_day(agent.PRICED_ON)}. The model is {model}. A rule asks nothing."),
            ".", " Its decisions fill each day's buyer list." if r["decider"] == leader else "")

    def chose(r):
        return hv(f"{r['chose_cheapest']:,} of {r['decisions']:,}", lost_tip(r))

    leader = week["ctx"].buyer and week["ctx"].buyer[0]
    ranked = sorted(claudes, key=lambda found: found[1]["dollars_lost"])    # the model that lost least leads; a tie keeps the listed order
    buyer_take = join(
        f"{ranked[0][1]['decider']} chose the cheapest action on ", chose(ranked[0][1]),
        " back-order cases, working from the facts alone and never a price.",
        *[piece for _, r in ranked[1:] for piece in (f" {r['decider']} chose it on ", chose(r), ".")]) if ranked else None
    buyer_bars = bars([(r["decider"], r["lost_a_decision"],
                        f"{cents(r['lost_a_decision'])} a decision"
                        + (f" · {r['could_not_be_done']} not possible" if r["could_not_be_done"] else ""),
                        lost_tip(r, f"\nAsking cost {cents(r['asking_dollars'])}." if r["asking_dollars"] else ""),
                        "lead" if r["decider"] in [c["decider"] for _, c in claudes] else
                        "control" if r is borrowed else "") for r in bought.values()])

    titles = {}    # each evidence card's heading by its query, for the links that open it

    def card(name, heading, text, fig, do, anchor=None, take=None, points=(), note=None):
        """One evidence card. anchor gives its heading a short id, for a link from outside the page. take is the card's
        finding in a line, set above its text; points are short lines under the text, and note closes them."""
        titles[name] = heading
        tip = f"{heading}\nSource: sql/metrics/{name}.sql. {about(name)}"
        named = f' id="{anchor}"' if anchor else ""
        finding = f'<p class="take">{take}</p>' if take else ""
        listed = '<ul class="points">' + "".join(f"<li>{point}</li>" for point in points) + "</ul>" if points else ""
        closing = f"<p>{note}</p>" if note else ""
        return (f'<section class="card" id="proof-{name}"><h3{named}{tipattr(tip)}>{e(heading)}</h3>{finding}<p>{text}</p>'
                f'{listed}{closing}{fig}<p class="do"><strong>What a fleet does with this:</strong> {do}</p></section>')

    proof = [
        card("dispatch_scorecard", "Who takes the call", join(
            "Every held-back day replayed with the same made-up sick calls, breakdowns and priority changes. Staple sends "
            "each call to the technician with the lowest expected cost: drive and repair minutes, plus a repeat visit times the "
            "chance that technician's skill or truck fails it, placing first the call with most to lose", q("regret insertion"),
            ". Shuffling the technicians' skills drops it to ",
            hv(pct(shuffled_skills["first_time_fix_pct"]), f"{pct(shuffled_skills['first_time_fix_pct'])} with shuffled skills\n"
               f"Each technician read another's success rate and speed; {cents(shuffled_skills['cost_per_call'])} a call."),
            ", below the parts-only rule's ",
            hv(pct(parts_only["first_time_fix_pct"]), f"{pct(parts_only['first_time_fix_pct'])} on parts alone\nEvery technician "
               f"taken as average; {cents(parts_only['cost_per_call'])} a call."),
            ", so what skills add to that rule comes from knowing who is good at what. Shuffling the history behind the parts odds it "
            "reads leaves it at ",
            hv(pct(shuffled_odds["first_time_fix_pct"]), f"{pct(shuffled_odds['first_time_fix_pct'])} with shuffled-history parts odds\n"
               f"Against {pct(skills['first_time_fix_pct'])} with no parts judgment at all. The control keeps "
               f"{kept(controls[dispatch.SHUFFLED_ODDS])} of that gain: it {controls[dispatch.SHUFFLED_ODDS]['verdict']}."),
            ", so that control does not fall back. The parts judgment earns most of its gain during the day, by sending a "
            "call that comes in or is moved to a truck already loaded for that appliance. That needs only what each "
            "appliance usually needs, which the shuffle keeps. Calls moved by a sick day or a breakdown were fixed first time ",
            hv(pct(ours["moved_fix_pct"]), f"{pct(ours['moved_fix_pct'])} of {ours['moved']} moved calls fixed first time\n"
               f"The nearest technician on today's stock, facing the same surprises: {pct(now['moved_fix_pct'])} of {now['moved']}."),
            f" of the time, against {pct(now['moved_fix_pct'])} for the current rules. A rolled call", q("rolled call"),
            " counts as not fixed."),
            figure("dispatch_scorecard", "Calls fixed on the first visit, with a rolled call counted as not fixed", dispatch_bars,
                   results["dispatch_scorecard"]),
            "keep the routing system dispatch already owns, and hand it this expected-cost score for each technician and call, "
            "so its routes carry the skill and parts judgment."),
        card("inventory_roi", "What to load tonight", join(
            "Each night the balanced truck ranks every part by its worth", q("worth"), " per unit of space. It covers ",
            hv(pct(bal["covered_pct"]), f"{pct(bal['covered_pct'])} of jobs covered\nAgainst {pct(fixed['covered_pct'])} on today's trucks."),
            " of jobs for ", hv(f"{cents(bal['cost_a_truck_day'])} a truck-day", f"{cents(bal['cost_a_truck_day'])} a truck-day\nRepeat "
                               f"${bal['repeat_cost']:.2f}, moves ${bal['handling_cost']:.2f}, holding ${bal['holding_cost']:.2f}."),
            q("truck-day"), f", against {pct(fixed['covered_pct'])} and ",
            hv(f"{cents(fixed['cost_a_truck_day'])}", f"{cents(fixed['cost_a_truck_day'])} a truck-day on today's stock\nRepeat "
               f"${fixed['repeat_cost']:.2f}, moves ${fixed['handling_cost']:.2f}, holding ${fixed['holding_cost']:.2f}."),
            " for today's fixed stock, while holding ",
            hv(f"${bal['stock_value']:,.0f}", f"${bal['stock_value']:,.0f} of stock on the balanced truck\n{bal['parts_aboard']:.0f} parts, "
               f"{some(bal['permanent_parts'])} of them aboard nearly every day."),
            f" against ${fixed['stock_value']:,.0f}. Three tests of that gain follow. Loaded from shuffled history, the control",
            q("control"), ", it still covers ",
            hv(pct(shuffled_load["covered_pct"]), f"{pct(shuffled_load['covered_pct'])} of jobs, loaded from shuffled history\n"
               f"{cents(shuffled_load['cost_a_truck_day'])} a truck-day. The shuffle keeps what each appliance usually needs "
               "and breaks every link to model family and problem."),
            f" for {cents(shuffled_load['cost_a_truck_day'])} and keeps ",
            hv(kept(controls[load.CONTROL_BALANCED]), f"{kept(controls[load.CONTROL_BALANCED])} of the saving kept\n"
               f"{cents(fixed['cost_a_truck_day'])} on today's stock, {cents(bal['cost_a_truck_day'])} balanced, "
               f"{cents(shuffled_load['cost_a_truck_day'])} from shuffled history: it {controls[load.CONTROL_BALANCED]['verdict']}.\n"
               "From controls.sql."),
            " of the saving. So that share comes from loading each truck for the appliances it will meet tomorrow, and "
            "the rest from the predictor's odds for each model family and problem. The balanced truck also fills ",
            hv(f"{bal['space_used']:.0f} units", f"{bal['space_used']} units of space used, of {world.CAPACITY} on a truck\n"
               f"Today's stock uses {fixed['space_used']}."),
            f" of space where today's stock uses {fixed['space_used']:.0f}; held to today's space it covers ",
            hv(pct(equal["covered_pct"]), f"{pct(equal['covered_pct'])} of jobs in the space today's stock uses\n"
               f"{cents(equal['cost_a_truck_day'])} a truck-day on {equal['space_used']} units, against "
               f"{cents(bal['cost_a_truck_day'])} with the whole truck."),
            f" for {cents(equal['cost_a_truck_day'])}. And the saving leans on the made-up ${load.MOVE:.0f} for moving one part: ",
            Raw(", ".join(f"at ${m:.0f} it costs " + hv(f"{cents(r['cost_a_truck_day'])}",
                          f"{cents(r['cost_a_truck_day'])} a truck-day when one move costs ${m:.0f}\nIt covers "
                          f"{pct(r['covered_pct'])} of jobs and moves {r['moves_a_night']} parts a night, against "
                          f"{bal['moves_a_night']} at ${load.MOVE:.0f}.") for m, r in priced)),
            (", each still under" if all(r["cost_a_truck_day"] < fixed["cost_a_truck_day"] for _, r in priced)
             else ", no longer all under"), f" today's {cents(fixed['cost_a_truck_day'])}."),
            figure("inventory_roi", "Cost a truck-day: repeat visits, parts moved overnight, and money tied up", load_bars,
                   results["inventory_roi"]),
            "switch the overnight load from a fixed stock list to tomorrow's booked calls, so the parts room works from a load "
            "sheet per truck, like the one the plan opens beside the board."),
        card("ai_step", "Claude coding the bookings", join(
            f"{triage.MODEL_NAME} ({triage.MODEL}) reads the customer's own words and picks a problem code with a confidence. "
            "On that code alone its 3-part list covers ",
            hv(pct(claude["coverage_pct"]), f"{pct(claude['coverage_pct'])} of jobs on Claude's code\nIts code matches the booked "
               f"code on {pct(claude['agrees_booked_pct'])} of bookings."),
            " of jobs, against ", hv(pct(booked["coverage_pct"]), f"{pct(booked['coverage_pct'])} with the booked code\nThe code a "
                                     "call-taker picked, taken as given."), " for the booked code", q("booked code"), " and ",
            hv(pct(keywords["coverage_pct"]), f"{pct(keywords['coverage_pct'])} for a plain keyword matcher\nThe code whose label "
               "shares the most words with the customer's. It is a floor to beat, not a picture of any booking screen."),
            " for a plain keyword matcher. These bookings describe their problems in only ",
            hv(f"{claude['phrasings']} ways", f"{said}\nThe invented customers say each problem a few ways, so every score here "
               "is a score over that many readings, weighted by how often each is said."),
            ", so each figure is a score over that many readings and not over thousands of separate ones. When Claude is "
            "unsure it asks one question. With a customer who always picks the matching answer, its list covers ",
            hv(pct(asked["coverage_pct"]), f"{pct(asked['coverage_pct'])} with ideal replies\nAn upper bound: the customer here is "
               "simulated and always picks the offered answer that matches the booking."),
            ", an upper bound. With random replies, the control, it covers ",
            hv(pct(random_replies["coverage_pct"]), f"{pct(random_replies['coverage_pct'])} with random replies\nThe control, and "
               "the lower end: the question only helps when the answer is real."),
            ". A real customer lands between the two. Shuffling Claude's answers between bookings drops its list to ",
            hv(pct(shuffled_ai["coverage_pct"]), f"{pct(shuffled_ai['coverage_pct'])} with shuffled answers\nThe control: the reading "
               "only helps when it belongs to the words."),
            ". The second chart holds Claude to the confidence it states. Where it states ", surest["stated_confidence"],
            ", its first reading matches the booked code on ",
            hv(pct(surest["agrees_booked_pct"]), f"{pct(surest['agrees_booked_pct'])} right at {surest['stated_confidence']}\n"
               f"{surest['jobs']:,} bookings in {surest['phrasings']} distinct phrasings."),
            " of bookings. Where it states ", least["stated_confidence"], ", on ",
            hv(pct(least["agrees_booked_pct"]), f"{pct(least['agrees_booked_pct'])} right at {least['stated_confidence']}\n"
               f"{least['jobs']:,} bookings in {least['phrasings']} distinct phrasings."),
            "." + cautious + " The answers are saved with the code, so these pages rebuild without a key."),
            figure("ai_step", "Jobs whose every needed part is on the 3-part list, by how the words were read", ai_bars, results["ai_step"])
            + figure("ai_calibration", "How often Claude's first reading matches the booked code, by the confidence it stated",
                     calibration_bars, results["ai_calibration"]),
            "let Claude code the bookings and ask its one question at the moment of booking, and keep a person on the few calls "
            "its question cannot settle."),
        card("backorder_exposure", "Back-orders to chase first", join(
            "A back-order", q("back-order"), " starts on one day and ships on another, so the list is made again every day: "
            "the days left count down, and a part drops off the day it ships. For each stuck part the list forecasts the "
            "calls that will need it before it ships, from training history only, and how many have no truck nearby "
            f"holding one. Across the {len(scored)} back-orders that shipped inside the held-back days it expected ",
            hv(f"{exp_calls:.1f} calls and {exp_stuck:.1f} stuck", f"{exp_calls:.1f} calls, {exp_stuck:.1f} stuck\n"
               f"${sum(r['dollars_at_risk'] for r in scored):,.0f} at risk across {len(scored)} back-orders, each scored "
               "on the first day it was listed."),
            "; the days then brought ", hv(f"{act_calls} and {act_stuck}", f"{act_calls} calls, {act_stuck} stuck\nCounted on the "
                                         "held-back days, which the forecast never saw."),
            f". A single booking is flagged when its odds of needing a stuck part reach {load.BACKORDER_FLAG:.0%}. Of "
            f"{flag['bookings']:,} held-back bookings, ",
            hv(f"{flag['flagged']} were flagged and {flag['flagged_and_stuck']} of those were stuck",
               f"{pct(flag['flags_right_pct'])} of flags marked a booking that was stuck\n{flag['were_stuck']} bookings were stuck "
               f"in all, against a forecast of {flag['forecast_stuck']}."),
            ". So the count can be forecast and the single booking cannot: a flag is a reason to warn a customer, never a "
            "prediction for that call."),
            figure("backorder_exposure", "Back-ordered parts on each day of this week, by repeat-visit dollars at risk", "",
                   results["backorder_exposure"], row_tip=stuck_tip, shown=stuck_week)
            + figure("backorder_flag", "The back-order flag on single bookings, scored on the held-back days", "",
                     results["backorder_flag"]),
            "expedite from the top of this list, and move spares toward the zones where no truck holds one."),
        card("buyer_scorecard", "Claude as the buyer", join(
            "Each case is one back-ordered part on one held-back day. The engine prices the buyer's four actions in "
            "expected dollars: send the calls anyway, expedite, move a part from another truck, or rebook. A decider",
            q("decider"), " loses what its choice costs over the cheapest.",
            " A model looks up counts, days and odds, and has to work out the dollars itself." if claudes else
            " No Claude model's decisions are saved for this prompt, so none is scored here; python -m staple.agent asks "
            "for them with the reader's own key."),
            figure("buyer_scorecard", "Dollars lost a decision over the cheapest action, by who decides", buyer_bars,
                   results["buyer_scorecard"]),
            "let a model work each back-order from the same lookups and draft the note, score its choices against the "
            "arithmetic every night, and keep the buyer's sign-off on every note before it goes out.", anchor="buyer",
            take=buyer_take,
            points=[model_point(m, r) for m, r in ranked] + [join(
                "For scale, sending anyway every time loses ", hv(f"${sends['dollars_lost']:,.0f}", lost_tip(sends)),
                " and expediting every time ", hv(f"${rushes['dollars_lost']:,.0f}", lost_tip(rushes)),
                ". A control", q("control"), " that takes the cheapest action for another case's facts loses ",
                hv(f"${borrowed['dollars_lost']:,.0f}", lost_tip(borrowed, "\nIts choice no longer rests on the case it is "
                   "made for, so a small loss has to come from reading each case's own facts.")), ".")],
            note=join("The cheapest action is plain arithmetic, so a model can tie the engine and never beat it. Nothing "
                      "here sends a note.")),
        card("carry_list_curve", "Which parts to carry", join(
            "The predictor ranks parts by how often they fixed the same model family and problem, and leans", q("lean"),
            " on the wider appliance group when a family's history is thin. Three parts on the carry list", q("carry list"),
            " cover ", hv(pct(three["predictor_pct"]), f"{pct(three['predictor_pct'])} of held-back jobs\nEvery needed part on a "
                          f"{load.SHOWN_K}-part list. With {points[-1]['k']} parts: {pct(points[-1]['predictor_pct'])}."),
            " of held-back jobs; the national top three cover ",
            hv(pct(three["national_pct"]), f"{pct(three['national_pct'])} with the national top {load.SHOWN_K}\nThe appliance's "
               "most-used parts, whatever the problem."), ". Shuffling the history drops the predictor to ",
            hv(pct(three["shuffled_pct"]), f"{pct(three['shuffled_pct'])} on shuffled history\nThe control falls to the national "
               "line, so the gain comes from what the history links."),
            ". Scored again with the held-back jobs already in its history, it covers ",
            hv(pct(three["leaked_pct"]), f"{pct(three['leaked_pct'])} with the held-back jobs leaked in\n"
               f"{three['leaked_pct'] - three['predictor_pct']:+.1f} points on the predictor's {pct(three['predictor_pct'])}. "
               "No scored method is built this way; the row only measures what the hold-out is worth."),
            f", {three['leaked_pct'] - three['predictor_pct']:+.1f} points. That gap is what holding the days back is worth. "
            "It is small here because the invented failure rates never change; on real history it is the gap to watch."),
            figure("carry_list_curve", "Held-back jobs whose every needed part is on the list", curve(points, calls),
                   results["carry_list_curve"]),
            "rank each booked call's parts by model family and problem rather than by national usage, and feed those lists to "
            "the overnight load."),
        card("parts_caused_reschedules", "Where repeat visits come from", join(
            "On today's trucks, a missing part causes ",
            hv(f"{100 * caused / repeats:.0f}%", f"{caused:,} of {repeats:,} repeat visits\nAcross {all_jobs:,} jobs. The rest failed "
               "on the technician's success rate with every part aboard."), " of repeat visits", q("repeat visit"), ". ",
            f"{worst['appliance'].capitalize()}s are worst: ",
            hv(f"{worst['parts_caused_per_100_jobs']:.0f} of every 100", f"{worst['parts_caused']:,} of {worst['jobs']:,} "
               f"{worst['appliance']} jobs\nOnly {pct(fill[worst['appliance']]['line_fill_pct'])} of the part lines they need "
               "are on the truck."), f" {worst['appliance']} jobs need a repeat visit because a part was not on board."),
            figure("parts_caused_reschedules", "Repeat visits caused by a missing part, per 100 jobs", cause_bars,
                   results["parts_caused_reschedules"]),
            f"start the pilot on {worst['appliance']}s, where a missing part costs the most repeat visits."),
        card("parts_cycle_time", "How long a repair waits for its parts", join(
            "A repair whose truck lacked a part waits for it: the part is ordered, the supplier takes its lead time, and "
            "the technician comes back. Those days are the parts cycle time", q("parts cycle time"), ". In the held-back history ",
            hv(pct(past["repairs_waiting_pct"]), f"{pct(past['repairs_waiting_pct'])} of {past['repairs']:,} held-back repairs waited\n"
               f"Counted from the order day and the repeat-visit day the history records. Each waited "
               f"{past['days_when_waiting']:.2f} days, which is {past['days_a_repair']:.2f} days spread over every repair."),
            f" of repairs waited, {past['days_when_waiting']:.2f} days each. Replayed over the same days, the current rules",
            q("current rules"), " leave ",
            hv(pct(wait_now["repairs_waiting_pct"]), f"{pct(wait_now['repairs_waiting_pct'])} of {wait_now['repairs']:,} repairs wait\n"
               f"The nearest technician on today's stock. Each waits {wait_now['days_when_waiting']:.2f} days, which is "
               f"{wait_now['days_a_repair']:.2f} days spread over every repair."),
            " waiting and Staple ",
            hv(pct(wait_ours["repairs_waiting_pct"]), f"{pct(wait_ours['repairs_waiting_pct'])} of {wait_ours['repairs']:,} repairs wait\n"
               f"Skills and parts, on balanced loads. Each waits {wait_ours['days_when_waiting']:.2f} days."),
            f". Spread over every repair, the wait falls from {wait_now['days_a_repair']:.2f} days to ",
            hv(f"{wait_ours['days_a_repair']:.2f}", f"{wait_ours['days_a_repair']:.2f} days a repair under Staple\n"
               f"Against {wait_now['days_a_repair']:.2f} under the current rules and {past['days_a_repair']:.2f} in the history as recorded."),
            ". Staple changes how many repairs wait, not the supplier's days: every row draws on the same made-up lead "
            "times, and no way of handing out calls shortens them. A repair that still waits under Staple waits ",
            hv(f"{wait_ours['days_when_waiting']:.2f} days", f"{wait_ours['days_when_waiting']:.2f} days for a repair that waits under Staple\n"
               f"Against {wait_now['days_when_waiting']:.2f} under the current rules. A part on back-order waits first for the "
               "day a center can ship it, then its lead time."),
            f" against {wait_now['days_when_waiting']:.2f}. That is longer because ",
            hv(pct(wait_ours["waits_on_backorder_pct"]), f"{pct(wait_ours['waits_on_backorder_pct'])} of Staple's waits are on a back-ordered part\n"
               f"Against {pct(wait_now['waits_on_backorder_pct'])} under the current rules. Staple removes most of the other "
               "waits, so the ones on back-order are a larger share of what is left."),
            f" of its waits are on a part no center can ship, against {pct(wait_now['waits_on_backorder_pct'])}. A truck "
            "cannot load a part on back-order", q("back-order"), ", so those are the waits a better load does not remove.", wait_less),
            figure("parts_cycle_time", "Days a repair waits for its parts, spread over every repair", cycle_bars,
                   results["parts_cycle_time"]),
            "measure it from two days the history already holds, the order day and the repeat-visit day, and send the "
            "back-ordered waits to the buyer's list, because loading cannot remove them."),
        card("controls", "Each gain against its control", join(
            "A control", q("control"), " is a method run again with one link in its data broken. Where the gain goes "
            f"with the link, it came from the link. {len(controls) - len(held)} of these {len(controls)} controls fall back, "
            f"each keeping under {metrics.FALLS_BACK:.0%} of its gain. ",
            (f"{counted(len(held), 'control')} {'does' if len(held) == 1 else 'do'} not: "
             + "; ".join(f"{r['control']} keeps {kept(r)}" for r in held)
             + ". Each of them shuffles the history, and that shuffle keeps what every appliance usually needs. So the "
               "share a control keeps is what knowing tomorrow's appliances is worth, and the rest is what the links to "
               "model family and problem add. The carry list's own control falls back because the list it is measured "
               "against, the national top parts, already knows the appliance."
             if held else "None keeps its gain.")),
            figure("controls", "The share of each gain its control keeps", control_bars, results["controls"]),
            "run every control on its own history before crediting a gain, and credit a gain to a link only where "
            "breaking that link takes the gain away."),
    ]
    adopt = ('<section class="panel"><h2' + tipattr("How a fleet would adopt it\nThe order I would run it in on real history, "
             "each step with the result that decides the next.") + '>How a fleet would adopt it</h2><ol class="steps">'
             '<li><strong>Back-test on its own history.</strong> The same replay on real calls, parts and technicians, before '
             'any truck changes. If the gains vanish there, it stops here.</li>'
             '<li><strong>Pilot one region.</strong> Load and dispatch one region by this plan for six weeks against a matched '
             'region left as it is, and compare first-time fix, parts-caused repeat visits and stock value.</li>'
             '<li><strong>Roll out region by region.</strong> Each region goes live only after the one before it beat its match.</li></ol></section>')
    limits = ('<section class="panel"><h2' + tipattr("What this shows, and what it does not\nThe instruction manual's last sections "
              "list every limit; the methods page names each corner cut on purpose and its upgrade.") + '>What this shows, and what it does not</h2>'
              f'<p>The plan for each day runs on Claude\'s codes, over one invented week. The scorecard and the evidence run on the '
              f'call-taker\'s booked codes for all {world.TEST_DAYS} held-back days, so they measure the planning apart from the '
              f'reading. Every result reproduces from one seed, and each gain is shown beside a control that breaks the link it '
              f'should come from: {len(controls) - len(held)} of the {len(controls)} fall back, and the last evidence card names the '
              f'{"one that does" if len(held) == 1 else "ones that do"} not. None of it shows the gains on a real fleet, '
              f'because the history is invented. The method cuts {len(method_corners())} corners on purpose, each marked in '
              f'the code and <a href="{FILES["method"]}#corners" target="_blank" rel="noopener">listed on the methods page</a> '
              'with the upgrade that would lift it. Among them: counting instead of a trained model, zone-to-zone drive '
              'times instead of road routing, and a call-by-call night plan instead of a solver.</p></section>')

    def pressed(on):
        return f' aria-pressed="{"true" if on else "false"}"'

    def shown(d):
        return d["day"] == open_day
    dbtn = []
    for d in week["days"]:
        m = d["morning"]
        sick, broken = len(m["sick"]), len(m["broken"])
        name = day_name(d["day"])
        events = ((f"{sick} sick call{'s' * (sick > 1)}" if sick else "no sick calls") + ", "
                  + (f"{broken} breakdown{'s' * (broken > 1)}" if broken else "no breakdowns"))
        weekday, _, date = name.replace(",", "").split()
        tip = f"{name}\n{int(d['ours']['calls'])} calls; {events}."
        dbtn.append(f'<button type="button" class="day-btn" data-day="{d["day"]}" data-name="{e(name)}"{pressed(shown(d))} '
                    f'aria-label="{e(name)}: {events}"{tipattr(tip)}>'
                    f'{weekday} {date}{"<i class=ev-s>&#10010;</i>" * sick}{"<i class=ev-b>&#9888;</i>" * broken}</button>')
    views = (("night", "Plan at night", "Routes and loads as planned the night before, from the calls booked by then."),
             ("ran", "As the day ran", f"After the {SICK_AT} sick calls, the breakdowns, the calls added on the day and the priority changes."))
    vbtn = "".join(f'<button type="button" class="vbtn" data-view="{v}"{pressed(v == "night")}{tipattr(t + chr(10) + h)}>{t}</button>'
                   for v, t, h in views)
    steps = "".join(f'<li{tipattr(t + chr(10) + h)}><strong>{e(t)}</strong>{e(s)}</li>' for t, s, h in (
        ("1. Claude codes every booking.", "It reads the customer's own words and picks the problem, asking one question when unsure.",
         f"{triage.MODEL_NAME}, on answers made once by the model and saved with the code. The AI at booking panel shows each unsure call."),
        ("2. Staple plans the trucks.", "Which technician takes each call, in what order, and what each truck loads tonight.",
         "The dispatch rule and the balanced truck, replayed call by call. The board, the map and the load sheet show it."),
        ("3. People act before 08:00.", "The parts room loads, the call center and dispatch ring customers, the buyer chases back-orders.",
         "The to-do list names each action, its owner and the truck or call it concerns.")))
    week_tip = (f"The plan for one day\nAn invented day, coded by Claude and planned call by call. The buttons pick one of the "
                f"most recent {plan.WEEK} held-back days, which are not hand-picked. The page opens on the most recent day with "
                "a sick call and a breakdown that both moved calls. ✚ marks a sick call, ⚠ a breakdown.")
    added = Counter(j["day"] for j in week["jobs"] if j["unplanned"])    # the calls that came in on each day
    daybar = (f'<div class="daybar" id="plan"><h2{tipattr(week_tip)}>The plan for <span class="day-name">{e(day_name(open_day))}</span></h2>'
              f'<div class="days" role="group" aria-label="Day">{"".join(dbtn)}</div>'
              f'<div class="views" role="group" aria-label="Board view">{vbtn}</div>'
              + "".join(day_line(d, added[d["day"]], shown(d)) for d in week["days"]) + "</div>")
    todos = "".join(todo_panel(d, shown(d), titles) for d in week["days"])
    boards = "".join(board_panel(d, ctx, shown(d)) for d in week["days"])
    maps = "".join(route_map(d, ctx, week["centers"], shown(d)) for d in week["days"])
    asked = "".join(ai_panel(d, ctx, shown(d)) for d in week["days"])
    changed = "".join(morning_panel(d, ctx, shown(d)) for d in week["days"])
    compared = "".join(day_proof(d, shown(d)) for d in week["days"])
    evidence_tip = (f"The evidence\n{len(proof)} cards over all {world.TEST_DAYS} held-back days. Each has its chart, the table "
                    "the chart is drawn from, and its control.")
    evidence = (f'<section class="evidence-head" id="evidence"><h2{tipattr(evidence_tip)}>The evidence</h2>'
                f'<p class="sub">{len(proof)} cards over all {world.TEST_DAYS} held-back days, on the call-taker\'s booked codes, '
                f'so they measure the planning apart from the reading. Each has its chart, the table the chart is drawn from, '
                f'and its control.</p></section><div class="evidence">{"".join(proof)}</div>')
    out = ' target="_blank" rel="noopener"'
    run_tip = "Run it yourself\nThe two commands that rebuild and check everything on this page, and where the code is kept."
    run = (f'<section class="panel" id="run"><h2{tipattr(run_tip)}>Run it yourself</h2>'
           f'<p>Staple needs Python {PYTHON[0]}.{PYTHON[1]} or newer and installs nothing. <code>python run.py</code> invents the '
           f'history and builds every figure and page here. <code>python check.py</code> then checks them, and plants faults to '
           f'prove its checks can fail. The code is at <a href="{REPO}"{out}>{REPO.split("//")[1]}</a>, and the '
           f'<a href="{FILES["manual"]}#run"{out}>instruction manual</a> walks through a first run.</p>'
           f'<p>Designed and directed by <a href="{SITE}"{out}>{AUTHOR}</a>, built with Claude Code.</p></section>')
    # The arrow a chosen route wears on each leg, drawn once here and used by every day's map.
    arrow = ('<svg class="defs" aria-hidden="true" focusable="false"><defs><marker id="leg" viewBox="0 0 8 8" refX="4" refY="4" '
             'markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path class="leg" d="M1 1 7 4 1 7z"/>'
             '</marker></defs></svg>')
    body = (f'{arrow}<div class="grid split-top">{scorecard(results)}<div class="slot">{todos}</div></div>{daybar}'
            f'<div class="grid work"><div class="grid">{boards}{compared}</div><div class="grid">{detail_panel(week, ctx, chosen)}{maps}</div></div>'
            f'<div class="grid two"><div class="grid">{asked}</div><div class="grid">{changed}</div></div>'
            f'<ol class="how">{steps}</ol>{evidence}<div class="grid pair">{adopt}{limits}</div>{run}')
    return shell("report", "Staple report", LEDE, body, stamp_text, day, chosen)


def as_sent(template):
    """A prompt template as the model receives it: doubled braces single, the {fields} still named."""
    return template.replace("{{", "{").replace("}}", "}")


def listed(values, word):
    """Values as a sentence lists them: 25, 45 and 30."""
    values = [str(v) for v in values]
    return f"{', '.join(values[:-1])} {word} {values[-1]}"


def months(numbers):
    """A run of months by name: November to February."""
    name = lambda m: dt.date(2000, m, 1).strftime("%B")
    return f"{name(numbers[0])} to {name(numbers[-1])}"


def facts():
    """Every value the manuals quote, read from the code that uses it."""
    cat, start = world.CATALOG, world.START
    n_parts, n_problems = [len(cat[t][1]) for t in cat], [len(cat[t][2]) for t in cat]
    v = {"jobs": f"{world.JOBS:,}", "days": f"{world.DAYS:,}", "start": f"{start:%b} {start.day}, {start.year}", "types": len(cat),
         "techs": world.TECHS, "test_days": world.TEST_DAYS, "seed": world.SEED, "families": world.FAMILIES_PER_TYPE,
         "rare_families": world.RARE_FAMILIES, "misdescribed": f"{world.MISDESCRIBED:.0%}", "random_failure": f"{world.RANDOM_FAILURE:.0%}",
         "vague": f"{world.VAGUE_WORDS:.0%}", "capacity": world.CAPACITY, "stock_fill": f"{world.STOCK_FILL:.0%}",
         "zones": len(world.ZONES), "parts_min": min(n_parts), "parts_max": max(n_parts), "problems_min": min(n_problems),
         "problems_max": max(n_problems), "pull": predict.PULL, "shown_k": load.SHOWN_K, "ks": f"{min(load.KS)} to {max(load.KS)}",
         "repeat_visit": f"${load.REPEAT_VISIT:.0f}", "move": f"${load.MOVE:.0f}", "holding_rate": f"{load.HOLDING_RATE:.0%}",
         "unplanned": f"{load.UNPLANNED:.0%}", "nearby": load.NEARBY_MINUTES, "lean_tolerance": cents(load.LEAN_TOLERANCE),
         "corners_method": len(method_corners()), "corners_checks": len(corners()) - len(method_corners()),
         "permanent": f"{load.PERMANENT:.0%}", "backorder_flag": f"{load.BACKORDER_FLAG:.0%}", "model": triage.MODEL,
         "effort": triage.EFFORT, "api_version": triage.API_VERSION, "blend_below": triage.BLEND_BELOW, "day": dispatch.DAY,
         "minute": f"${dispatch.MINUTE:.0f}", "skill_pull": dispatch.SKILL_PULL, "sick": f"{dispatch.SICK:.0%}", "breakdown": f"{dispatch.BREAKDOWN:.0%}",
         "escalate": f"{dispatch.ESCALATE:.0%}", "ease": f"{dispatch.EASE:.0%}",
         "REPAIR": ", ".join(f"{t} {m}" for t, m in dispatch.REPAIR.items()),
         "URGENT": ", ".join(sorted(dispatch.URGENT)), "LOW": ", ".join(sorted(dispatch.LOW)),
         "SYSTEM": triage.SYSTEM, "TEMPLATE": as_sent(triage.TEMPLATE), "FOLLOWUP_SYSTEM": triage.FOLLOWUP_SYSTEM,
         "FOLLOWUP_TEMPLATE": as_sent(triage.FOLLOWUP_TEMPLATE), "week": plan.WEEK, "likely": f"{plan.LIKELY:.0%}",
         "behind": f"{plan.BEHIND:.0%}", "backorder_starts": f"{world.BACKORDER_STARTS:.0%}",
         "backorder_days": f"{world.BACKORDER_DAYS[0]} to {world.BACKORDER_DAYS[1]}",
         "order_next_day": f"{world.ORDER_NEXT_DAY:.0%}",
         "lead_times": listed([f"{plan.days(d)} ({share:.0%} of orders)" for d, share in world.LEAD_TIMES.items()], "or"),
         "same_day_until": plan.clock(dispatch.SAME_DAY_UNTIL), "buyer_items": plan.BUYER_ITEMS,
         "expedite": f"${buyer.EXPEDITE:.0f}", "expedite_days": buyer.EXPEDITE_DAYS, "wait_day": f"${buyer.WAIT_DAY:.0f}",
         "falls_back": f"{metrics.FALLS_BACK:.0%}", "move_steps": ", ".join(f"${m:.0f}" for m in load.MOVE_STEPS),
         "furnace_winter": f"{world.FURNACE_WINTER:g}", "furnace_summer": f"{world.FURNACE_SUMMER:g}",
         "winter_months": months(world.WINTER), "summer_months": months(world.SUMMER), "weak_part": world.WEAK_PART,
         "no_part": world.NO_PART, "one_part": f"{world.ONE_PART_UNTIL - world.NO_PART:.2f}",
         "levels": listed(list(world.LEVELS), "or"), "level_weights": listed([w for w, _, _ in world.LEVELS.values()], "and"),
         "level_rates": listed([r for _, r, _ in world.LEVELS.values()], "or"),
         "level_times": listed([f"{t:.2f}" for _, _, t in world.LEVELS.values()], "or"), "specialist": world.SPECIALIST,
         "specialist_types": listed(world.SPECIALIST_TYPES, "or"), "specialist_rate": world.SPECIALIST_RATE,
         "specialist_time": world.SPECIALIST_TIME, "rate_spread": world.RATE_SPREAD, "time_spread": f"{world.TIME_SPREAD:.0%}",
         "top_rate": world.TOP_RATE, "mid_certified": world.MID_CERTIFIED, "grid": f"{world.GRID[0]} by {world.GRID[1]}",
         "zone_miles": f"{world.ZONE_MILES:g}", "inside_zone": f"{world.INSIDE_ZONE:g}", "between_zones": f"{world.BETWEEN_ZONES:g}",
         "minutes_a_mile": f"{world.MINUTES_A_MILE:g}", "centers": len(world.CENTERS), "shuffle_seed": predict.SHUFFLE_SEED,
         "unplanned_seed": load.UNPLANNED_SEED, "dispatch_seed": dispatch.SEED, "skills_seed": dispatch.SKILLS_SEED,
         "answer_seed": triage.SHUFFLE_SEED, "id_length": triage.ID_LENGTH, "day_start": plan.clock(0),
         "day_end": plan.clock(dispatch.DAY), "sick_by": SICK_AT, "breaks_from": plan.clock(dispatch.BREAKS_BETWEEN[0]),
         "breaks_to": plan.clock(dispatch.BREAKS_BETWEEN[1]), "max_k": max(load.KS),
         "shuffled_history": dispatch.SHUFFLED_HISTORY, "queries": len(list(metrics.SQL.glob("*.sql"))), "python": ".".join(map(str, PYTHON)),
         "BUYER_SYSTEM": agent.SYSTEM, "BUYER_OPENING": agent.OPENING, "buyer_rounds": agent.MAX_ROUNDS,
         "buyer_effort": agent.EFFORT, "buyer_priced_on": pretty_day(agent.PRICED_ON)}
    return {k: Raw(e(str(x))) for k, x in v.items()}


def thresholds():
    made, design = "made up: replace with a real figure", "design choice"
    items = [("Held-back days", f"{world.TEST_DAYS} days", "The back-test's hold-out.", design, "principles", None),
             ("Pull toward wider groups", f"{predict.PULL} jobs", "How far thin history leans on the wider level.", "design choice; check.py backtest measures its effect", "predictor", "ex-predictor"),
             ("Carry list quoted", f"{load.SHOWN_K} parts", "The list length the report quotes.", design, "backtest", "ex-backtest"),
             ("Repeat visit", f"${load.REPEAT_VISIT:.0f}", "Another visit to a job the first visit did not fix.", made, "loads", None),
             ("Moving a part", f"${load.MOVE:.0f}", "Loading or unloading one part overnight.", made, "loads", None),
             ("Higher move prices replayed", ", ".join(f"${m:.0f}" for m in load.MOVE_STEPS), "Other prices for one move, each replayed to show how far the balanced truck's saving leans on that price.", made, "loads", None),
             ("A control falls back", f"under {metrics.FALLS_BACK:.0%} of the gain kept", "A control keeping less than this share of the gain it tests counts as fallen back.", design, "principles", None),
             ("Holding rate", f"{load.HOLDING_RATE:.0%} a year", "Cost of money tied up in stock.", made, "loads", None),
             ("Unplanned share", f"{load.UNPLANNED:.0%}", "Calls added or moved on the day.", made, "loads", None),
             ("Nearby", f"{load.NEARBY_MINUTES} minutes", "Another truck counts as nearby within this drive.", design, "availability", None),
             ("List-length tolerance", f"{cents(load.LEAN_TOLERANCE)} a truck-day", "The shortest list within this of the cheapest is chosen.", design, "loads", "ex-loads"),
             ("Permanent", f"{load.PERMANENT:.0%} of days", "A part aboard this often counts as permanent.", design, "loads", None),
             ("Back-order flag", f"{load.BACKORDER_FLAG:.0%} odds", "A booking at this chance of needing a stuck part is flagged.", made, "availability", "ex-availability"),
             ("Truck space", f"{world.CAPACITY} units", "Space on one truck.", made, "world", None),
             ("Today's fill", f"{world.STOCK_FILL:.0%}", "How full today's trucks are kept.", made, "world", None),
             ("Back-order starts", f"{world.BACKORDER_STARTS:.0%} a day", "Chance a part the centers stock goes on back-order.", made, "world", None),
             ("Back-order length", f"{world.BACKORDER_DAYS[0]} to {world.BACKORDER_DAYS[1]} days", "How long a back-order lasts before the part ships.", made, "world", None),
             ("Buyer's list", f"{plan.BUYER_ITEMS} parts", "Back-orders shown to the buyer each day, costliest first.", design, "plan", None),
             ("Expedite fee", f"${buyer.EXPEDITE:.0f}", "The fee to have a back-ordered part shipped early.", made, "buyer", "ex-buyer"),
             ("Expedited shipping", f"{buyer.EXPEDITE_DAYS} days", "How soon an expedited part ships.", made, "buyer", None),
             ("A day's wait", f"${buyer.WAIT_DAY:.0f}", "Each day a rebooked call waits for its part.", made, "buyer", None),
             ("AI confidence", f"{triage.BLEND_BELOW}", "Below it, a reading leans on the appliance's overall odds and a follow-up question is asked.", design, "ai", "ex-ai"),
             ("Pull toward the appliance's average", f"{dispatch.SKILL_PULL} jobs", "How far a technician with few visits leans on the appliance's average success rate.", design, "dispatch", None),
             ("Working day", f"{dispatch.DAY} minutes", "08:00 to 17:00.", design, "dispatch", None),
             ("Minute", f"${dispatch.MINUTE:.0f}", "One minute of a technician and truck.", made, "dispatch", "ex-dispatch"),
             ("Sick day", f"{dispatch.SICK:.0%} a day", "Chance a technician calls out.", made, "dispatch", None),
             ("Breakdown", f"{dispatch.BREAKDOWN:.0%} a day", "Chance a truck breaks down.", made, "dispatch", None),
             ("Same-day calls", f"until {plan.clock(dispatch.SAME_DAY_UNTIL)}", "An unplanned call comes in between 08:00 and this time.", made, "dispatch", None),
             ("Priority change", f"{dispatch.ESCALATE:.0%} up, {dispatch.EASE:.0%} down", "Chance a call escalates or eases on the day.", made, "dispatch", None)]
    items += [(f"Repair time, {t}", f"{m} minutes", "An average technician's time on this appliance.", made, "dispatch", None) for t, m in dispatch.REPAIR.items()]
    def rule(name, section, example):
        tip = (f"{name}\nOpens the methods page in a new tab at "
               + ("the worked example of this rule." if example else "the section that uses this value."))
        return (f'<a href="{FILES["method"]}#{example or section}" target="_blank" rel="noopener"{tipattr(tip)}>'
                f'{e(name)}</a>')
    body = "".join(f"<tr><td>{rule(a, s, x)}</td><td>{e(b)}</td><td>{e(c)}</td><td>{e(d)}</td></tr>"
                   for a, b, c, d, s, x in items)
    return Raw(f'<div class="scroll"><table class="wrap"><thead><tr><th>Threshold</th><th>Value</th><th>What it does</th>'
               f'<th>What sets it</th></tr></thead><tbody>{body}</tbody></table></div>')


MARKER = "# ponytail:"    # the comment that marks a corner cut on purpose


def corner_files():
    """The source a corner can be marked in: the Python and the shell scripts."""
    return [ROOT / "run.py", ROOT / "check.py", *sorted((ROOT / "staple").glob("*.py")), *sorted((ROOT / "scripts").rglob("*.sh"))]


def corners(paths=None):
    """Each corner cut on purpose, as (file, words), read from the comment that marks it. Only a comment counts:
    the marker inside a string, as in this file, is not a corner."""
    found = []
    for path in paths or corner_files():
        if path.suffix == ".py":
            with tokenize.open(path) as source:
                notes = [(t.start, t.string) for t in tokenize.generate_tokens(source.readline) if t.type == tokenize.COMMENT]
        else:
            # ponytail: a shell script is read line by line, so a line that opens with the marker inside a quoted
            # block of text would count as a corner. The upgrade is to read shell scripts with a shell tokenizer.
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            notes = [((i, len(line) - len(line.lstrip())), line.strip()) for i, line in enumerate(lines, 1)
                     if line.lstrip().startswith("#")]
        for i, ((row, col), words) in enumerate(notes):
            if words.startswith(MARKER):
                text = [words[len(MARKER):].strip()]
                for at, more in notes[i + 1:]:    # the comment runs on while the next line is a comment at the same indent
                    if at != (row + len(text), col) or more.startswith(MARKER):
                        break
                    text.append(more.lstrip("#").strip())
                found.append((path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.name, " ".join(text)))
    return found


def method_corners():
    """The corners in the method itself, apart from those in the checks and in this file, which builds the pages."""
    return [c for c in corners() if c[0].startswith("staple/") and c[0] != "staple/report.py"]


def corner_list():
    """The corners as the methods page lists them: the method's first, then those of the checks and the build."""
    found, method = corners(), method_corners()

    def items(group):
        return "".join(f"<li><code>{e(name)}</code>: {e(words)}</li>" for name, words in group)
    return Raw(f"<h3>In the method</h3><ul>{items(method)}</ul><h3>In the checks and the build</h3>"
               f"<ul>{items([c for c in found if c not in method])}</ul>")


def checks():
    names = re.findall(r'@check\("([^"]+)"', (ROOT / "check.py").read_text(encoding="utf-8"))
    return Raw("<dl>" + "".join(f"<dt><code>{e(n)}</code></dt><dd>{e(CHECK_ABOUT[n])}</dd>" for n in names) + "</dl>")


def sql_metrics():
    out = []
    for path in sorted(metrics.SQL.glob("*.sql")):
        out.append(f'<h3 id="sql-{e(path.stem)}">{e(path.stem)}</h3><p>{e(about(path.stem))}</p>'
                   f'<pre>{e(path.read_text(encoding="utf-8").strip())}</pre>')
    return Raw("".join(out))


def example_html(x):
    """One worked example, always in this order: words, inputs, steps, Staple's own answer, then any formula."""
    head = "".join(f"<th>{e(h)}</th>" for h in x.inputs[0])
    body = "".join("<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in row) + "</tr>" for row in x.inputs[1])
    formula = f'<p class="ex-lead">{e(x.lead)}</p><code class="formula">{e(x.formula)}</code>' if x.formula else ""
    return Raw(f'<div class="example" id="{x.id}"><h3>Worked example: {e(x.title)}</h3><p class="ex-words">{e(x.words)}</p>'
               f'<div class="scroll"><table class="ex-inputs wrap"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
               f'<ol class="ex-steps">{"".join(f"<li>{e(s)}</li>" for s in x.steps)}</ol>'
               f'<p class="ex-answer">Staple\'s answer: <strong>{e(x.show(x.compute()))}</strong>, worked out on every build by '
               f'<code>{e(x.source)}</code> on the inputs above.</p>{formula}</div>')


def fill_in(template, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], (PAGES / template).read_text(encoding="utf-8"))


def build_manuals(stamp_text, day):
    v = facts()
    v["sample_q"] = Raw(tipattr(SAMPLE_Q))
    v["MARKS"] = Raw("".join(f"<tr><td>{e(m)}</td><td>{e(MARK_HELP[m][0])}</td><td>{e(MARK_HELP[m][1])}</td></tr>" for m in plan.WHERE))
    v["MARK_ORDER"] = Raw("".join(f"<li><strong>{e(m)}</strong>: {e(MARK_HELP[m][0])}</li>" for m in load.MARKS))
    v["THRESHOLDS"] = thresholds()
    v["GLOSSARY"] = Raw("<dl>" + "".join(f'<dt id="{slug(t)}"><strong>{e(t)}</strong></dt><dd>{e(d)}</dd>'
                                         for t, d in sorted(GLOSSARY.items())) + "</dl>")
    v["SQL_METRICS"], v["CHECKS"], v["CORNERS"] = sql_metrics(), checks(), corner_list()
    v.update({f"EX_{x.section}": example_html(x) for x in examples.EXAMPLES})
    manual = shell("manual", "Staple instruction manual", "What each figure means, how to read it, and what it cannot tell you.",
                   fill_in("manual.html", v), stamp_text, day)
    method = shell("method", "Staple methods page", "Every formula, rule, prompt, query and check behind the report.",
                   fill_in("method.html", v), stamp_text, day)
    return manual, method


def grid(head, rows):
    return Raw('<div class="scroll"><table class="wrap"><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in head)
               + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in r) + "</tr>" for r in rows)
               + "</tbody></table></div>")


def folders(paths):
    n = Counter(p.rsplit("/", 1)[0] + "/" if "/" in p else "the top folder" for p in paths)
    return ", ".join(f"{f} {k}" for f, k in sorted(n.items(), key=lambda kv: (kv[0] == "the top folder", kv[0])))


def languages_json(found=None):
    """The sentence and counts the sites quote, published as languages.json so neither site types a count."""
    code = (found or languages())["code"]
    return {"sentence": written_in(), "counts": {lang: len(p) for lang, p in code.items()},
            "metric_queries": sum(p.startswith("sql/metrics/") for p in code["SQL"]), "page": PAGES_URL + FILES["languages"]}


def build_languages(stamp_text, day):
    found = languages()
    code = {lang: paths for lang, paths in found["code"].items() if paths}
    where = {lang: title for _, title, langs, _ in PLACES for lang in langs}
    places = "".join(
        f'<section id="{pid}"><h2>{e(title)}</h2><p>{e(words)}</p>' + "".join(
            f'<h3>{e(lang)}</h3><p>{e(LANGUAGE_ABOUT[lang][0])}</p><p>{e(LANGUAGE_ABOUT[lang][1])}</p>'
            f'<p class="sub">{counted(len(code[lang]), "file")}: {e(folders(code[lang]))}.</p>' for lang in langs if lang in code)
        + "</section>" for pid, title, langs, words in PLACES)
    v = {"SENTENCE": Raw(e(written_in())),
         "MADE_OF": grid(("Language", "Files", "Where it runs"), [(lang, len(p), where.get(lang, "no section")) for lang, p in code.items()]),
         "PLACES": Raw(places),
         "PROGRAM_COUNT": counted(len(PROGRAMS), "program"),
         "PROGRAMS": Raw("<dl>" + "".join(f"<dt><strong>{e(n)}</strong></dt><dd>{e(p)}</dd>" for n, p in PROGRAMS.values()) + "</dl>"),
         "NOT_CODE": grid(("Kind", "Files", "Which"), [(k, len(p), ", ".join(p)) for k, p in found["formats"].items()]),
         "PUBLISHED": Raw(e(", ".join([*FILES.values(), *ASSETS, LANGUAGES_JSON])))}
    return shell("languages", "Staple code languages", "What Staple is written in, where each part runs, and every program it starts.",
                 fill_in("languages.html", v), stamp_text, day)


def build(results, week, stamp_text, day):
    manual, method = build_manuals(stamp_text, day)
    return {"report": build_report(results, week, stamp_text, day), "manual": manual, "method": method,
            "languages": build_languages(stamp_text, day)}


def write(results, week, folder=db.OUT):
    text, day, _, warnings = stamp()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for kind, page in build(results, week, text, day).items():
        (folder / FILES[kind]).write_text(page, encoding="utf-8")
    for name, data in assets().items():
        (folder / name).write_bytes(data)
    (folder / LANGUAGES_JSON).write_text(json.dumps(languages_json(), indent=1) + "\n", encoding="utf-8")
    if folder == PUBLISHED and text.endswith("+"):
        warnings.append("the published copy names unsaved changes; commit the code, then rerun --pages and commit docs/")
    return warnings
