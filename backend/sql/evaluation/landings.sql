-- The landing: where a watched pick sits in the ordering, engine against owner.
--
-- The ground truth, and the one signal in Anchor that is a real taste judgment rather
-- than a mood proxy. When the owner watches a film something put in front of them, they
-- rate it, and where it lands says what they actually thought. The claim is always the
-- comparison, and never a fixed number (evaluation.md).
--
-- Two comparisons, because the spec makes the same claim twice and the watch event stamps
-- both halves of the provenance:
--
--   * the ranked tier - `engine_*` (stood in the up-next zone or the pool when logged)
--     against `owner_*` (pinned, or plain backlog). A pinned film is the owner's pick and
--     never the engine's (ADR 0012).
--   * the discovery feed - `discovery_*` (the film entered through an accept) against
--     `hand_added_*`. Import-seeded films are in neither arm: nobody picked them.
--
-- The two axes read the same watches from different sides, so one watch can count in
-- `engine_*` and in `discovery_*` at once - an accepted film that took a tier seat before
-- it was watched is honestly both.
--
-- `landing` is the share of the ordering the film sits at or above: 1.0 is the top of the
-- wall, and higher is better. Read the two arms of an axis against each other, within a
-- window.
--
-- The window is a stretch of the watch clock, passed as :watches_per_window, because the
-- whole comparison is "same window" and a window here is a run of the owner's own
-- activity. It is numbered off every watch event the account has, imported history
-- included, because that is what the watch clock is - so a back catalogue imported at the
-- start fills the early windows and is never compared against picks the engine made a
-- year later, against a wall that had grown underneath.
--
-- Read at computation time on purpose. The ordering is joined as it stands right now, so
-- a rate-later placement made months after the watch completes the fact whenever it
-- happens, and a later move counts. `*_unplaced` is the watches whose fact is still open -
-- watched, not yet placed - and it is the honest context for a mean taken over the ones
-- that are closed.
--
-- Rewatches are left out: a rewatched film was placed before this watch happened, so its
-- landing is a judgment the pick had no part in. So are the watches that arrived with a
-- seed import, which are history rather than anybody's pick.

WITH ordering AS (
    SELECT
        p.account_id,
        af.film_id,
        (
            count(*) OVER (PARTITION BY p.account_id)
            - row_number() OVER (PARTITION BY p.account_id ORDER BY p.band DESC, p.rank ASC)
            + 1
        )::numeric / count(*) OVER (PARTITION BY p.account_id) AS landing
    FROM placements p
    JOIN account_films af ON af.id = p.account_film_id
),
clocked AS (
    SELECT
        w.account_id,
        w.film_id,
        w.standing,
        w.origin,
        w.rewatch,
        w.watched_at,
        row_number() OVER (PARTITION BY w.account_id ORDER BY w.watched_at, w.id) AS watch_clock
    FROM watch_events w
),
watches AS (
    SELECT
        c.account_id,
        c.film_id,
        c.standing IN ('up_next', 'pool') AS engine_sourced,
        c.origin,
        (c.watch_clock - 1) / greatest((:watches_per_window)::integer, 1) AS window_index
    FROM clocked c
    JOIN accounts a ON a.id = c.account_id
    WHERE NOT a.is_demo AND NOT c.rewatch AND c.watched_at > a.created_at
)
SELECT
    w.account_id,
    w.window_index,
    count(*) FILTER (WHERE w.engine_sourced AND o.film_id IS NOT NULL) AS engine_placed,
    avg(o.landing) FILTER (WHERE w.engine_sourced) AS engine_landing,
    count(*) FILTER (WHERE w.engine_sourced AND o.film_id IS NULL) AS engine_unplaced,
    count(*) FILTER (WHERE NOT w.engine_sourced AND o.film_id IS NOT NULL) AS owner_placed,
    avg(o.landing) FILTER (WHERE NOT w.engine_sourced) AS owner_landing,
    count(*) FILTER (WHERE NOT w.engine_sourced AND o.film_id IS NULL) AS owner_unplaced,
    count(*) FILTER (
        WHERE w.origin = 'discovery_accept' AND o.film_id IS NOT NULL
    ) AS discovery_placed,
    avg(o.landing) FILTER (WHERE w.origin = 'discovery_accept') AS discovery_landing,
    count(*) FILTER (
        WHERE w.origin = 'discovery_accept' AND o.film_id IS NULL
    ) AS discovery_unplaced,
    count(*) FILTER (
        WHERE w.origin = 'hand_added' AND o.film_id IS NOT NULL
    ) AS hand_added_placed,
    avg(o.landing) FILTER (WHERE w.origin = 'hand_added') AS hand_added_landing,
    count(*) FILTER (
        WHERE w.origin = 'hand_added' AND o.film_id IS NULL
    ) AS hand_added_unplaced
FROM watches w
LEFT JOIN ordering o ON o.account_id = w.account_id AND o.film_id = w.film_id
GROUP BY w.account_id, w.window_index
ORDER BY w.account_id, w.window_index;
