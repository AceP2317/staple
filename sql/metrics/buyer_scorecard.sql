-- The buyer's scorecard: each decider's choices over every back-ordered part of every held-back day, priced by
-- the engine. A decider is charged the action it named and loses the dollars that costs over the cheapest
-- action; a choice that cannot be carried out is charged as sending anyway, and counted. A row named for a
-- Claude model holds that model's saved decisions, made from lookups that return counts, days and odds and
-- never a price. One misreading counts the wrong choices that are the cheapest once expediting is charged
-- the calls that come after the early ship day, where the rule charges those before it. Asking is what the
-- Claude API charged for a model's decisions at the listed prices. A rule asks nothing and writes no note,
-- so its last four columns are zero.
SELECT decider,
       decisions,
       invalid                                AS could_not_be_done,
       matched                                AS chose_cheapest,
       misread                                AS one_misreading_explains,
       ROUND(cost, 0)                         AS cost_of_choices,
       ROUND(lost, 0)                         AS dollars_lost,
       ROUND(lost / decisions, 2)             AS lost_a_decision,
       ROUND(asking, 2)                       AS asking_dollars,
       ROUND(100.0 * asking / decisions, 2)   AS asking_cents_a_decision,
       notes_short,
       ran_on                                 AS replies_run_on
FROM buyer_score
ORDER BY rowid;
