-- The scorecard: five measures of the whole plan, replayed over the held-back days. Each is given under the fleet's
-- current rules (the nearest technician on today's stock), under Staple, and under Staple's control, which runs
-- Staple on shuffled history. The difference, and the share of it the control keeps, are worked from the figures
-- as shown. Parts-caused reschedules count the calls served whose truck lacked a needed part. Fill rate is the
-- share of the part lines those calls needed that were aboard. Inventory return is the repeat-visit dollars a
-- year the stock avoids per dollar of stock aboard, counting only calls that needed a part.
WITH plan AS (         -- the five measures for every replayed plan
  SELECT d.policy,
         100.0 * d.fixes_per_call                       AS first_time_fix,
         100.0 * d.parts_missing                        AS reschedules,
         100.0 * d.line_fill                            AS fill_rate,
         d.wait_per_call                                AS cycle_time,
         d.part_jobs * a.value * 365 / d.stock_value    AS inventory_return
  FROM dispatch d JOIN assumptions a ON a.name = 'repeat_visit'
),
long AS (              -- one row per plan and measure
  SELECT policy, 1 AS shown_order, first_time_fix AS value FROM plan
  UNION ALL SELECT policy, 2, reschedules FROM plan
  UNION ALL SELECT policy, 3, fill_rate FROM plan
  UNION ALL SELECT policy, 4, cycle_time FROM plan
  UNION ALL SELECT policy, 5, inventory_return FROM plan
),
named (shown_order, measure, unit, better_when, places) AS (
  VALUES (1, 'first-time fix', 'percent of calls', 'higher', 1),
         (2, 'parts-caused reschedules', 'per 100 calls served', 'lower', 1),
         (3, 'fill rate', 'percent of part lines', 'higher', 1),
         (4, 'parts cycle time', 'days a repair', 'lower', 2),
         (5, 'inventory return', 'dollars a year per dollar of stock', 'higher', 2)
),
shown AS (
  SELECT n.shown_order, n.measure, n.unit, n.better_when, n.places,
         ROUND(MAX(CASE WHEN l.policy = 'nearest technician, today''s stock' THEN l.value END), n.places)        AS current_rules,
         ROUND(MAX(CASE WHEN l.policy = 'Staple: skills and parts, balanced loads' THEN l.value END), n.places) AS staple,
         ROUND(MAX(CASE WHEN l.policy = 'Staple on shuffled history (control)' THEN l.value END), n.places)     AS control
  FROM named n JOIN long l USING (shown_order)
  GROUP BY n.shown_order
)
SELECT measure, unit, better_when, current_rules, staple,
       ROUND(staple - current_rules, places)                                    AS difference,
       control,
       ROUND(100.0 * (control - current_rules) / (staple - current_rules), 0)   AS control_keeps_pct
FROM shown
ORDER BY shown_order;
