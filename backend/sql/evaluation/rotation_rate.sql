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
-- The denominator is the watches since that counter started, which is why the row carries
-- `rotations_counted_from`. A counter that began mid-life over every watch there has ever
-- been would read an account's whole imported back catalogue as watches its tier was
-- passed over during, and would understate the rate for good. Watches that arrived with an
-- import are set aside here for the same reason they are in tier adoption: they sit before
-- the account existed, and nothing could have been rotating during them.
--
-- Multiply by N for the per-N-watches reading. Nothing here is a number to hit: a high
-- rate on a deep backlog is the tier doing its job, and a high rate on a shallow one is
-- churn. Telling those apart is a human judgment, and the only judgment form there is.

WITH counted AS (
    SELECT
        a.id AS account_id,
        coalesce(t.staleness_rotations, 0) AS staleness_rotations,
        coalesce(t.rotations_counted_from, 0) AS rotations_counted_from,
        count(w.id) FILTER (WHERE w.watched_at > a.created_at) AS anchor_logged_watches,
        count(w.id) FILTER (WHERE w.watched_at <= a.created_at) AS watches_before_the_account
    FROM accounts a
    LEFT JOIN tier_states t ON t.account_id = a.id
    LEFT JOIN watch_events w ON w.account_id = a.id
    WHERE NOT a.is_demo
    GROUP BY a.id, t.staleness_rotations, t.rotations_counted_from
)
SELECT
    account_id,
    staleness_rotations,
    greatest(anchor_logged_watches - rotations_counted_from, 0) AS logged_watches,
    anchor_logged_watches,
    watches_before_the_account,
    rotations_counted_from AS watches_before_counting_began,
    staleness_rotations::numeric
        / nullif(greatest(anchor_logged_watches - rotations_counted_from, 0), 0)
        AS rotations_per_watch
FROM counted
ORDER BY account_id;
