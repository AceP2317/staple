-- Parts-caused reschedules by appliance: repeat visits that happened because a needed part was not on the truck.
-- A repeat with every part on board failed on the technician's success rate, the only other cause this made-up history records.
SELECT type                                                              AS appliance,
       COUNT(*)                                                          AS jobs,
       SUM(first_time_fix = 0)                                          AS repeat_visits,
       SUM(first_time_fix = 0 AND parts_on_truck = 0)                   AS parts_caused,
       ROUND(100.0 * SUM(first_time_fix = 0 AND parts_on_truck = 0) / COUNT(*), 1)
                                                                         AS parts_caused_per_100_jobs,
       ROUND(100.0 * SUM(first_time_fix = 0 AND parts_on_truck = 0) / SUM(first_time_fix = 0), 1)
                                                                         AS parts_share_of_repeats_pct
FROM jobs
GROUP BY type
ORDER BY parts_caused_per_100_jobs DESC;
