-- The made-up world, one SQLite file. Every row is invented.

CREATE TABLE appliance_types (type TEXT PRIMARY KEY);

CREATE TABLE problems (
  code  TEXT PRIMARY KEY,           -- e.g. refrigerator.FF_WARM
  type  TEXT NOT NULL REFERENCES appliance_types(type),
  label TEXT NOT NULL
);

CREATE TABLE parts (
  part   TEXT PRIMARY KEY,          -- e.g. refrigerator/defrost heater
  type   TEXT NOT NULL REFERENCES appliance_types(type),
  name   TEXT NOT NULL,
  cost   REAL NOT NULL,             -- dollars, made up
  size   INTEGER NOT NULL,          -- truck space units, made up
  locked INTEGER NOT NULL           -- 1 = only a certified technician's truck may carry it
);

CREATE TABLE families (
  family    TEXT PRIMARY KEY,       -- a model family, e.g. RF-210
  type      TEXT NOT NULL REFERENCES appliance_types(type),
  weak_part TEXT REFERENCES parts(part),
  first_day TEXT NOT NULL           -- the day this family first appears in the field
);

CREATE TABLE zones (zone INTEGER PRIMARY KEY, x REAL NOT NULL, y REAL NOT NULL);

CREATE TABLE techs (
  tech        TEXT PRIMARY KEY,     -- T01..T30
  level       TEXT NOT NULL,        -- junior, mid, senior
  profile     TEXT NOT NULL,        -- generalist or specialist
  home_zone   INTEGER NOT NULL REFERENCES zones(zone),
  certified   INTEGER NOT NULL      -- may carry and fit locked parts
);

CREATE TABLE tech_skills (
  tech         TEXT NOT NULL REFERENCES techs(tech),
  type         TEXT NOT NULL REFERENCES appliance_types(type),
  success_rate REAL NOT NULL,       -- chance of fixing it first visit when the parts are there
  speed        REAL NOT NULL,       -- repair-time multiplier; below 1 is faster
  PRIMARY KEY (tech, type)
);

CREATE TABLE trucks (
  truck    TEXT PRIMARY KEY,
  tech     TEXT NOT NULL REFERENCES techs(tech),
  capacity INTEGER NOT NULL         -- space units
);

CREATE TABLE truck_stock (
  truck TEXT NOT NULL REFERENCES trucks(truck),
  part  TEXT NOT NULL REFERENCES parts(part),
  qty   INTEGER NOT NULL,
  PRIMARY KEY (truck, part)
);

CREATE TABLE centers (center TEXT PRIMARY KEY, zone INTEGER NOT NULL REFERENCES zones(zone));

-- The calendar of back-orders across the held-back days. No center can ship the part from the day a
-- back-order starts until the day it ships; on every other day a center has it.
CREATE TABLE backorders (
  part   TEXT NOT NULL REFERENCES parts(part),
  starts TEXT NOT NULL,             -- the first day no center can ship it
  ships  TEXT NOT NULL,             -- the day a center can ship it again
  PRIMARY KEY (part, starts)
);

CREATE TABLE jobs (
  job              INTEGER PRIMARY KEY,
  day              TEXT NOT NULL,
  zone             INTEGER NOT NULL REFERENCES zones(zone),
  type             TEXT NOT NULL REFERENCES appliance_types(type),
  family           TEXT NOT NULL REFERENCES families(family),
  booked_problem   TEXT NOT NULL REFERENCES problems(code),  -- what the customer described
  true_problem     TEXT NOT NULL REFERENCES problems(code),  -- known only because the data is made up
  words            TEXT NOT NULL,   -- the customer's own words at booking
  noise            TEXT NOT NULL,   -- none, misdescribed or random: known only because it is made up
  tech             TEXT NOT NULL REFERENCES techs(tech),
  parts_on_truck   INTEGER NOT NULL, -- 1 if every needed part was on that truck
  first_time_fix  INTEGER NOT NULL,
  split            TEXT NOT NULL    -- train (first 33 months) or test (last 3)
);

CREATE TABLE job_parts (
  job  INTEGER NOT NULL REFERENCES jobs(job),
  part TEXT NOT NULL REFERENCES parts(part),
  PRIMARY KEY (job, part)
);

CREATE INDEX jobs_split_type ON jobs(split, type);
CREATE INDEX job_parts_job ON job_parts(job);

-- Every made-up dollar and rate the results rest on, written from the code's own constants.
CREATE TABLE assumptions (name TEXT PRIMARY KEY, value REAL NOT NULL, meaning TEXT NOT NULL);

-- Results, written by the back-test.
CREATE TABLE backtest (method TEXT NOT NULL, k INTEGER NOT NULL, coverage REAL NOT NULL, PRIMARY KEY (method, k));
CREATE TABLE backtest_groups (grp TEXT NOT NULL, method TEXT NOT NULL, k INTEGER NOT NULL,
                              jobs INTEGER NOT NULL, coverage REAL NOT NULL);

-- Results, written by the truck-load replay. Every figure is an average per truck-day; dollars are made up.
CREATE TABLE load_policy (
  policy TEXT NOT NULL, k INTEGER NOT NULL,      -- k = carry-list parts per job; 0 for today's fixed stock
  truck_days INTEGER NOT NULL, jobs REAL NOT NULL, coverage REAL NOT NULL,
  saved REAL NOT NULL,                            -- jobs that needed a part and had every one aboard
  parts REAL NOT NULL, space REAL NOT NULL, value REAL NOT NULL, -- value is stock held, not money spent
  moves REAL NOT NULL,                            -- parts put on or taken off overnight
  permanent REAL NOT NULL,                        -- parts on board at least 90% of a truck's days
  repeat_cost REAL NOT NULL, handling REAL NOT NULL, holding REAL NOT NULL, total REAL NOT NULL,
  roi REAL NOT NULL,                              -- repeat dollars a year avoided per dollar of truck stock
  PRIMARY KEY (policy, k)
);
CREATE TABLE availability (kind TEXT NOT NULL, measure TEXT NOT NULL, value REAL NOT NULL,
                           PRIMARY KEY (kind, measure));

-- The AI step on held-back jobs: how each reading of the customer's words agrees with the booked code
-- and the true problem, and the coverage of the 3-part carry list it leads to.
CREATE TABLE triage (method TEXT PRIMARY KEY, jobs INTEGER NOT NULL, agree_booked REAL NOT NULL,
                     agree_true REAL NOT NULL, coverage REAL NOT NULL);

-- Claude's first reading on the held-back jobs, by the confidence it stated: how many distinct phrasings and
-- jobs fall in each band, and how often the reading agrees with the booked code and with the true problem.
CREATE TABLE triage_calibration (low REAL PRIMARY KEY, high REAL NOT NULL, phrasings INTEGER NOT NULL,
                                 jobs INTEGER NOT NULL, agree_booked REAL NOT NULL, agree_true REAL NOT NULL);

-- The dispatch replay over every held-back day, with the same made-up sick calls, breakdowns and priority
-- changes for every policy. A call rolled to tomorrow counts as not fixed today in fixes_per_call.
CREATE TABLE dispatch (
  policy TEXT PRIMARY KEY, calls INTEGER NOT NULL, served INTEGER NOT NULL, rolled INTEGER NOT NULL,
  fix_rate REAL NOT NULL,                         -- expected first-time fixes per call served
  fixes_per_call REAL NOT NULL, parts_missing REAL NOT NULL, drive_per_call REAL NOT NULL,
  moved INTEGER NOT NULL,                         -- calls moved to another truck by a sick call or breakdown
  moved_fix_rate REAL NOT NULL, cost_per_call REAL NOT NULL  -- minutes at $1 plus expected repeat visits, made up
);

-- The buyer's back-order list for every held-back day: each part no center can ship that day. Forecasts use
-- training only. The actual columns count what the held-back days then brought before the part shipped.
CREATE TABLE backorder_list (
  day TEXT NOT NULL, part TEXT NOT NULL REFERENCES parts(part),
  days_left INTEGER NOT NULL,                     -- days until a center can ship it
  holders TEXT NOT NULL,                          -- technicians whose trucks stock one today
  open_zones INTEGER NOT NULL,                    -- zones with no holder based within 30 minutes
  expected_calls REAL NOT NULL, expected_stuck REAL NOT NULL, at_risk REAL NOT NULL,
  actual_calls INTEGER NOT NULL, actual_stuck INTEGER NOT NULL,
  first_listed INTEGER NOT NULL,                  -- 1 on the first held-back day this back-order is listed
  whole_wait INTEGER NOT NULL,                    -- 1 when it ships inside the held-back days, so the actual columns are complete
  PRIMARY KEY (day, part)
);
