-- The AI step: how each reading of the customer's own words agrees with the booked code and with the true
-- problem, and the first-visit coverage of the 3-part list it leads to. The true problem is known only
-- because the data is made up; real history knows only the booked code. The made-up customers say each
-- problem only a few ways, and the phrasings column counts the distinct descriptions these jobs hold: every
-- score here is a score over that many readings, weighted by how often each is said. The row marked upper
-- bound rests on a simulated customer who always picks the answer that matches the booking.
SELECT method,
       jobs,
       (SELECT COUNT(*) FROM (SELECT DISTINCT type, words FROM jobs WHERE split = 'test')) AS phrasings,
       ROUND(100.0 * agree_booked, 1)  AS agrees_booked_pct,
       ROUND(100.0 * agree_true, 1)    AS agrees_true_pct,
       ROUND(100.0 * coverage, 1)      AS coverage_pct
FROM triage
ORDER BY rowid;
