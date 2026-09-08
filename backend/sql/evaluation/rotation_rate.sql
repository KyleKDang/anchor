-- Rotation rate: staleness demotions per watch.
--
-- Staleness is measured in owner activity and never in calendar time (watchlist.md): a
-- tier film passed over for enough logged watches rotates out with a re-entry cooldown.
-- So the rate is rotations against the same clock those rotations were measured in.
--
-- The numerator is a counter on the tier's own row rather than a count of anything,
-- because a rotation leaves nothing behind. The re-entry mark it writes is overwritten by
-- the next one, is written identically by a displacement and by a not-now, and is cleared
-- the moment the film takes a seat again.
--
-- Multiply by N for the per-N-watches reading. Nothing here is a target: a high rate on a
-- deep backlog is the tier doing its job, and a high rate on a shallow one is churn - the
-- difference is a human judgment, which is the only judgment form this file supports.

SELECT
    a.id AS account_id,
    coalesce(t.staleness_rotations, 0) AS staleness_rotations,
    coalesce(w.logged_watches, 0) AS logged_watches,
    coalesce(t.staleness_rotations, 0)::numeric
        / nullif(w.logged_watches, 0) AS rotations_per_watch
FROM accounts a
LEFT JOIN tier_states t ON t.account_id = a.id
LEFT JOIN (
    SELECT account_id, count(*) AS logged_watches
    FROM watch_events
    GROUP BY account_id
) w ON w.account_id = a.id
WHERE NOT a.is_demo
ORDER BY a.id;
