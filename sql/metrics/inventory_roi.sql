-- Inventory ROI by truck-load policy: repeat-visit dollars a year that a truck's stock avoids, per dollar of
-- stock on board, over the held-back replay. A job counts as a repeat visit avoided only when it needed a part
-- and every part was aboard, because an empty truck covers a job that needs none. Rows run from the cheapest
-- truck-day to the costliest: the return is a ratio the smallest stock wins, and the cost is what a fleet pays.
-- Every policy the replay ran is here, with the shuffled-history controls and the balanced truck's tests:
-- held to the space today's stock uses, and with one move priced higher. Every dollar is made up; the
-- assumptions table lists each one.
SELECT l.policy,
       l.k                                                   AS parts_per_job,
       ROUND(100.0 * l.coverage, 1)                          AS covered_pct,
       ROUND(l.saved, 2)                                     AS part_jobs_covered,
       ROUND(l.space, 1)                                     AS space_used,
       ROUND(l.parts, 1)                                     AS parts_aboard,
       ROUND(l.permanent, 1)                                 AS permanent_parts,
       ROUND(l.moves, 1)                                     AS moves_a_night,
       ROUND(l.value, 0)                                     AS stock_value,
       ROUND(l.repeat_cost, 2)                               AS repeat_cost,
       ROUND(l.handling, 2)                                  AS handling_cost,
       ROUND(l.holding, 2)                                   AS holding_cost,
       ROUND(l.total, 2)                                     AS cost_a_truck_day,
       ROUND(l.saved * a.value * 365 / l.value, 2)           AS roi
FROM load_policy l
JOIN assumptions a ON a.name = 'repeat_visit'
ORDER BY l.total, l.policy, l.k;
