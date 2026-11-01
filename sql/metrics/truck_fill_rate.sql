-- Truck fill rate by appliance: of the parts jobs needed, the share already on the technician's truck.
-- Line fill counts each needed part. Job fill counts a job only when all of its parts were there,
-- so one missing line fails the whole job, the way one short line holds a whole order.
WITH lines AS (
  SELECT j.job, j.type, s.part IS NOT NULL AS on_truck
  FROM job_parts jp
  JOIN jobs j USING (job)
  JOIN trucks t ON t.tech = j.tech
  LEFT JOIN truck_stock s ON s.truck = t.truck AND s.part = jp.part
),
by_job AS (
  SELECT type, MIN(on_truck) AS all_on_truck FROM lines GROUP BY job
)
SELECT l.type                                         AS appliance,
       COUNT(*)                                       AS part_lines,
       ROUND(100.0 * AVG(l.on_truck), 1)              AS line_fill_pct,
       (SELECT COUNT(*) FROM by_job b WHERE b.type = l.type) AS jobs_needing_parts,
       ROUND(100.0 * (SELECT AVG(all_on_truck) FROM by_job b WHERE b.type = l.type), 1) AS job_fill_pct
FROM lines l
GROUP BY l.type
ORDER BY line_fill_pct;
