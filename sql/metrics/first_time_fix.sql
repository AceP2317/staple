-- First-time fix by month: the share of repair jobs fixed on the first visit, from the made-up history.
-- A job is fixed first time only when every part it needed was on the truck and the repair took.
SELECT substr(day, 1, 7)                       AS month,
       COUNT(*)                                AS jobs,
       ROUND(100.0 * AVG(first_time_fix), 1)  AS first_time_fix_pct
FROM jobs
GROUP BY month
ORDER BY month;
