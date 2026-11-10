-- First-visit coverage against carry-list length: the share of held-back jobs where every needed part was on
-- a list of k parts, for the predictor, the national top-parts list and the shuffled-history control. The
-- leaked column scores the predictor again with the held-back jobs already in its history; its gap to the
-- predictor is what holding those days back is worth.
SELECT k,
       ROUND(100.0 * MAX(CASE WHEN method = 'predictor' THEN coverage END), 1)                AS predictor_pct,
       ROUND(100.0 * MAX(CASE WHEN method = 'national top parts' THEN coverage END), 1)       AS national_pct,
       ROUND(100.0 * MAX(CASE WHEN method = 'shuffled-history control' THEN coverage END), 1) AS shuffled_pct,
       ROUND(100.0 * MAX(CASE WHEN method = 'predictor that has seen the held-back jobs (leak test)'
                              THEN coverage END), 1)                                          AS leaked_pct,
       ROUND(100.0 * (SELECT coverage FROM backtest WHERE method = 'today''s trucks'), 1)     AS todays_trucks_pct
FROM backtest
WHERE k > 0
GROUP BY k
ORDER BY k;
