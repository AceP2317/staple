-- Back-order exposure: each back-ordered part, the calls expected to need it before it arrives, how many of
-- those have no truck nearby holding one, and the repeat-visit dollars at risk. Forecasts use training only;
-- the actual columns count what the held-back days then did.
SELECT part,
       eta_days,
       holders,
       open_zones,
       ROUND(expected_calls, 1)  AS expected_calls,
       ROUND(expected_stuck, 1)  AS expected_stuck,
       ROUND(at_risk, 0)         AS dollars_at_risk,
       actual_calls,
       actual_stuck
FROM backorder_list
ORDER BY at_risk DESC, part;
