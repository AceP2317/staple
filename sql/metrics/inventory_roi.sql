-- Inventory ROI by truck-load policy: repeat-visit dollars a year that a truck's stock avoids, per dollar of
-- stock on board, over the held-back replay. Each covered job counts as one repeat visit avoided against an
-- empty truck. Every dollar is made up; the assumptions table lists each one.
SELECT l.policy,
       l.k                                                   AS parts_per_job,
       ROUND(100.0 * l.coverage, 1)                          AS covered_pct,
       ROUND(l.value, 0)                                     AS stock_value,
       ROUND(l.total, 2)                                     AS cost_a_truck_day,
       ROUND(l.coverage * l.jobs * a.value * 365 / l.value, 2) AS roi
FROM load_policy l
JOIN assumptions a ON a.name = 'repeat_visit'
WHERE l.policy NOT LIKE '%shuffled%'
ORDER BY roi DESC;
