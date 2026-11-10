-- Parts cycle time: the days a repair waits for its parts, from the visit that lacked one to the repeat visit.
-- The first row is the held-back history as recorded. Each other row is one way of handing out calls, replayed over
-- the same days: a repair that lacked a part waits the made-up order lag and lead times, averaged over every way
-- they can fall. days_when_waiting is the wait of a repair that waited; days_a_repair spreads it over every repair.
WITH missing AS (      -- each part a held-back repair needed that its technician's truck did not hold
  SELECT j.job, j.day, jp.part
  FROM jobs j JOIN job_parts jp USING (job) JOIN trucks t ON t.tech = j.tech
  LEFT JOIN truck_stock s ON s.truck = t.truck AND s.part = jp.part
  WHERE j.split = 'test' AND s.part IS NULL
),
held AS (              -- the repairs that lacked a part no center could ship on the day of the visit
  SELECT DISTINCT m.job
  FROM missing m JOIN backorders b ON b.part = m.part AND b.starts <= m.day AND m.day < b.ships
),
recorded AS (          -- every held-back repair; days is empty where it had its parts
  SELECT julianday(w.repeat_visit_day) - julianday(j.day) AS days, h.job IS NOT NULL AS held
  FROM jobs j LEFT JOIN waits w USING (job) LEFT JOIN held h USING (job)
  WHERE j.split = 'test'
)
SELECT policy, repairs, repairs_waiting_pct, days_when_waiting, days_a_repair, waits_on_backorder_pct
FROM (
  SELECT 0 AS place, 'history as recorded' AS policy,
         COUNT(*)                                    AS repairs,
         ROUND(100.0 * COUNT(days) / COUNT(*), 1)    AS repairs_waiting_pct,
         ROUND(100.0 * SUM(held AND days IS NOT NULL) / COUNT(days), 1) AS waits_on_backorder_pct,
         ROUND(AVG(days), 2)                         AS days_when_waiting,
         ROUND(SUM(days) / COUNT(*), 2)              AS days_a_repair
  FROM recorded
  UNION ALL
  SELECT rowid, policy, served, ROUND(100.0 * parts_missing, 1), ROUND(100.0 * backorder_missing / parts_missing, 1),
         ROUND(wait_per_call / parts_missing, 2), ROUND(wait_per_call, 2)
  FROM dispatch
)
ORDER BY place;
