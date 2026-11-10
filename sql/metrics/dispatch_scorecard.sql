-- Dispatch scorecard: each way of handing out calls, replayed over the held-back days with the same made-up
-- sick calls, breakdowns and priority changes. A call rolled to tomorrow counts as not fixed today.
SELECT policy,
       calls,
       rolled,
       ROUND(100.0 * fixes_per_call, 1)   AS first_time_fix_pct,
       ROUND(100.0 * parts_missing, 1)    AS parts_missing_pct,
       ROUND(drive_per_call, 1)           AS drive_minutes,
       moved,
       ROUND(100.0 * moved_fix_rate, 1)   AS moved_fix_pct,
       ROUND(cost_per_call, 2)            AS cost_per_call
FROM dispatch
ORDER BY rowid;
