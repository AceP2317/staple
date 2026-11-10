-- The back-order flag on single bookings, scored on the held-back days. A booking is flagged when its odds of
-- needing a part that no center can ship and no truck nearby holds reach the level the assumptions table names
-- backorder_flag. The forecast is the sum of those odds over every booking, so it says how many bookings will
-- be stuck and not which ones: most flags mark a booking that turns out fine.
SELECT CAST(MAX(CASE WHEN measure = 'jobs' THEN value END) AS INTEGER)      AS bookings,
       ROUND(MAX(CASE WHEN measure = 'expected' THEN value END), 1)         AS forecast_stuck,
       CAST(MAX(CASE WHEN measure = 'blocked' THEN value END) AS INTEGER)   AS were_stuck,
       CAST(MAX(CASE WHEN measure = 'flagged' THEN value END) AS INTEGER)   AS flagged,
       CAST(MAX(CASE WHEN measure = 'caught' THEN value END) AS INTEGER)    AS flagged_and_stuck,
       ROUND(100.0 * MAX(CASE WHEN measure = 'caught' THEN value END)
                   / MAX(CASE WHEN measure = 'flagged' THEN value END), 1)  AS flags_right_pct
FROM availability
WHERE kind = 'jobs';
