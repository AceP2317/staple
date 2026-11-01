-- Parts-caused reschedules by appliance: repeat visits that happened because a needed part was not on the truck.
-- A repeat with every part on board failed for another reason, such as the diagnosis or the technician's skill.
SELECT type                                                              AS appliance,
       COUNT(*)                                                          AS jobs,
       SUM(first_visit_fix = 0)                                          AS repeat_visits,
       SUM(first_visit_fix = 0 AND parts_on_truck = 0)                   AS parts_caused,
       ROUND(100.0 * SUM(first_visit_fix = 0 AND parts_on_truck = 0) / COUNT(*), 1)
                                                                         AS parts_caused_per_100_jobs,
       ROUND(100.0 * SUM(first_visit_fix = 0 AND parts_on_truck = 0) / SUM(first_visit_fix = 0), 1)
                                                                         AS parts_share_of_repeats_pct
FROM jobs
GROUP BY type
ORDER BY parts_caused_per_100_jobs DESC;
