-- How often Claude's first reading is right at each level of confidence it states, on the held-back jobs. A
-- reader that can be trusted is right at least as often as the confidence it states. Below the confidence
-- the assumptions table names ai_confidence, Staple does not run on the code alone: the reading leans on the
-- appliance's overall odds, and Claude asks its one question. Each band also counts the distinct phrasings
-- its jobs hold, because the made-up customers say each problem only a few ways.
SELECT printf('%.2f to %.2f', c.low, c.high)  AS stated_confidence,
       c.phrasings,
       c.jobs,
       ROUND(100.0 * c.agree_booked, 1)       AS agrees_booked_pct,
       ROUND(100.0 * c.agree_true, 1)         AS agrees_true_pct,
       CASE WHEN c.low >= a.value THEN 'runs on the code' ELSE 'asks its question' END AS staple_then
FROM triage_calibration c
JOIN assumptions a ON a.name = 'ai_confidence'
ORDER BY c.low DESC;
