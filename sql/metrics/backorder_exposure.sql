-- Back-order exposure: the buyer's list for every held-back day. Each row is a part no center can ship that
-- day, with the calls expected to need it before it ships, how many of those have no truck nearby holding one,
-- and the repeat-visit dollars at risk. Forecasts use training only. The actual columns count what the held-back
-- days then brought before the part shipped. back_test marks the rows the forecast is scored on: the first day
-- a back-order is listed, when it ships inside the held-back days.
SELECT day,
       part,
       days_left                 AS ships_in_days,
       CASE WHEN holders = '' THEN 0 ELSE length(holders) - length(replace(holders, ' ', '')) + 1 END
                                 AS trucks_holding,
       open_zones,
       ROUND(expected_calls, 1)  AS expected_calls,
       ROUND(expected_stuck, 1)  AS expected_stuck,
       ROUND(at_risk, 0)         AS dollars_at_risk,
       actual_calls,
       actual_stuck,
       first_listed AND whole_wait AS back_test
FROM backorder_list
ORDER BY day, at_risk DESC, part;
