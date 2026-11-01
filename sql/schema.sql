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

CREATE TABLE centres (centre TEXT PRIMARY KEY, zone INTEGER NOT NULL REFERENCES zones(zone));

CREATE TABLE centre_stock (
  part     TEXT PRIMARY KEY REFERENCES parts(part),
  status   TEXT NOT NULL,           -- in_stock or backorder
  eta_days INTEGER                  -- for a back-order, days until it can ship
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
  first_visit_fix  INTEGER NOT NULL,
  split            TEXT NOT NULL    -- train (first 33 months) or test (last 3)
);

CREATE TABLE job_parts (
  job  INTEGER NOT NULL REFERENCES jobs(job),
  part TEXT NOT NULL REFERENCES parts(part),
  PRIMARY KEY (job, part)
);

CREATE INDEX jobs_split_type ON jobs(split, type);
CREATE INDEX job_parts_job ON job_parts(job);

-- Results, written by the back-test.
CREATE TABLE backtest (method TEXT NOT NULL, k INTEGER NOT NULL, coverage REAL NOT NULL, PRIMARY KEY (method, k));
CREATE TABLE backtest_groups (grp TEXT NOT NULL, method TEXT NOT NULL, k INTEGER NOT NULL,
                              jobs INTEGER NOT NULL, coverage REAL NOT NULL);
