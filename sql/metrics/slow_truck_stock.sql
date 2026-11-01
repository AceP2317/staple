-- Slow truck stock: what each truck holds that its own technician's jobs did not need in the 12 months
-- before the held-back months. Stock held is money tied up, not money spent.
WITH cutoff AS (
  SELECT MIN(day) AS test_from, date(MIN(day), '-365 days') AS year_before FROM jobs WHERE split = 'test'
),
used AS (
  SELECT DISTINCT j.tech, jp.part
  FROM job_parts jp JOIN jobs j USING (job) JOIN cutoff c
  WHERE j.day >= c.year_before AND j.day < c.test_from
)
SELECT t.truck,
       t.tech,
       COUNT(*)                                                          AS parts_on_board,
       ROUND(SUM(p.cost * s.qty), 0)                                     AS stock_value,
       SUM(u.part IS NULL)                                               AS parts_unused,
       ROUND(SUM(CASE WHEN u.part IS NULL THEN p.cost * s.qty ELSE 0 END), 0) AS unused_value,
       ROUND(100.0 * SUM(CASE WHEN u.part IS NULL THEN p.cost * s.qty ELSE 0 END) / SUM(p.cost * s.qty), 1)
                                                                         AS unused_value_pct
FROM truck_stock s
JOIN trucks t USING (truck)
JOIN parts p USING (part)
LEFT JOIN used u ON u.tech = t.tech AND u.part = s.part
GROUP BY t.truck
ORDER BY unused_value DESC, t.truck;
