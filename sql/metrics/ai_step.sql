-- The AI step: how each reading of the customer's own words agrees with the booked code and with the true
-- problem, and the first-visit coverage of the 3-part list it leads to. The true problem is known only
-- because the data is made up; real history knows only the booked code.
SELECT method,
       jobs,
       ROUND(100.0 * agree_booked, 1)  AS agrees_booked_pct,
       ROUND(100.0 * agree_true, 1)    AS agrees_true_pct,
       ROUND(100.0 * coverage, 1)      AS coverage_pct
FROM triage
ORDER BY rowid;
