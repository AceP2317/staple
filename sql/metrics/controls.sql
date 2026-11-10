-- Every control beside the gain it tests. A control is a method run again with one link in its data broken:
-- history shuffled, skills shuffled, answers shuffled, or replies made random. Each row gives the measure
-- without the method, with it, and with its control, then the share of the gain the control keeps, worked
-- from the three figures as shown. A control falls back when it keeps less than the share the assumptions
-- table names falls_back. A gain whose control does not fall back does not come from the link that was broken.
WITH tests (shown_order, control, gain_tested, measure, places, without_it, with_it, with_control) AS (
  SELECT 1, 'shuffled-history control', 'the predictor over the national top parts', 'jobs the quoted carry list covers, %', 1,
         100.0 * MAX(CASE WHEN b.method = 'national top parts' THEN b.coverage END),
         100.0 * MAX(CASE WHEN b.method = 'predictor' THEN b.coverage END),
         100.0 * MAX(CASE WHEN b.method = 'shuffled-history control' THEN b.coverage END)
  FROM backtest b JOIN assumptions q ON q.name = 'carry_list_quoted' AND b.k = q.value
  UNION ALL
  SELECT 2, 'top-up from shuffled history', 'topping today''s stock up with the day''s lists', 'cost a truck-day, $', 2,
         MIN(CASE WHEN policy = 'today''s trucks' THEN total END),
         MIN(CASE WHEN policy = 'today''s stock plus the day''s lists' THEN total END),
         MIN(CASE WHEN policy = 'top-up from shuffled history' THEN total END)
  FROM load_policy
  UNION ALL
  SELECT 3, 'balanced from shuffled history', 'the balanced truck over today''s stock', 'cost a truck-day, $', 2,
         MIN(CASE WHEN policy = 'today''s trucks' THEN total END),
         MIN(CASE WHEN policy = 'balanced truck' THEN total END),
         MIN(CASE WHEN policy = 'balanced from shuffled history' THEN total END)
  FROM load_policy
  UNION ALL
  SELECT 4, 'Staple with shuffled skills (control)', 'knowing each technician''s skill', 'calls fixed on the first visit, %', 1,
         100.0 * MAX(CASE WHEN policy = 'parts only, balanced loads' THEN fixes_per_call END),
         100.0 * MAX(CASE WHEN policy = 'Staple: skills and parts, balanced loads' THEN fixes_per_call END),
         100.0 * MAX(CASE WHEN policy = 'Staple with shuffled skills (control)' THEN fixes_per_call END)
  FROM dispatch
  UNION ALL
  SELECT 5, 'Staple with shuffled-history parts odds (control)', 'the dispatcher judging each truck''s parts',
         'calls fixed on the first visit, %', 1,
         100.0 * MAX(CASE WHEN policy = 'skills only, balanced loads' THEN fixes_per_call END),
         100.0 * MAX(CASE WHEN policy = 'Staple: skills and parts, balanced loads' THEN fixes_per_call END),
         100.0 * MAX(CASE WHEN policy = 'Staple with shuffled-history parts odds (control)' THEN fixes_per_call END)
  FROM dispatch
  UNION ALL
  SELECT 6, 'Claude answers shuffled (control)', 'Claude reading the customer''s words', 'jobs the 3-part list covers, %', 1,
         100.0 * MAX(CASE WHEN method = 'no code' THEN coverage END),
         100.0 * MAX(CASE WHEN method = 'Claude' THEN coverage END),
         100.0 * MAX(CASE WHEN method = 'Claude answers shuffled (control)' THEN coverage END)
  FROM triage
  UNION ALL
  SELECT 7, 'Claude, asking when unsure, random replies (control)', 'Claude asking one question when unsure',
         'jobs the 3-part list covers, %', 1,
         100.0 * MAX(CASE WHEN method = 'Claude' THEN coverage END),
         100.0 * MAX(CASE WHEN method = 'Claude, asking when unsure, ideal replies (upper bound)' THEN coverage END),
         100.0 * MAX(CASE WHEN method = 'Claude, asking when unsure, random replies (control)' THEN coverage END)
  FROM triage
),
shown AS (
  SELECT shown_order, control, gain_tested, measure, ROUND(without_it, places) AS without_it, ROUND(with_it, places) AS with_it,
         ROUND(with_control, places) AS with_control
  FROM tests
)
SELECT s.control, s.gain_tested, s.measure, s.without_it, s.with_it, s.with_control,
       ROUND(100.0 * (s.with_control - s.without_it) / (s.with_it - s.without_it), 0) AS gain_kept_pct,
       CASE WHEN (s.with_control - s.without_it) / (s.with_it - s.without_it) < a.value
            THEN 'falls back' ELSE 'does not fall back' END                          AS verdict
FROM shown s
JOIN assumptions a ON a.name = 'falls_back'
ORDER BY s.shown_order;
