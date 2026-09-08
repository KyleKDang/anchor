-- The landing: where a watched pick sits in the ordering, engine against owner.
--
-- The ground truth, and the one signal in Anchor that is a real taste judgment rather
-- than a mood proxy. When the owner watches a film the tier put in front of them, they
-- rate it, and where it lands says what they actually thought. The claim is always the
-- comparison - engine picks land higher than the owner's own same-window picks - and
-- never a fixed number (evaluation.md).
--
-- `landing` is the share of the ordering the film sits at or above: 1.0 is the top of the
-- wall, and higher is better. Read the two columns against each other within a window.
--
-- The window is a stretch of watches, passed as :watches_per_window, because the whole
-- comparison is "same window" and a window here is a run of the owner's own activity.
-- Windowing is what keeps a back catalogue imported at the start from being compared
-- against picks the engine made a year later, against a wall that had grown underneath.
--
-- Read at computation time on purpose. The ordering is joined as it stands right now, so
-- a rate-later placement made months after the watch completes the fact whenever it
-- happens, and a later move counts. `engine_unplaced` and `owner_unplaced` are the
-- watches whose fact is still open - watched, not yet placed - and they are the honest
-- context for a mean taken over the ones that are closed.
--
-- Rewatches are left out. A rewatched film was placed before this watch happened, so its
-- landing is a judgment the pick had no part in.
--
-- A pinned film is the owner's pick and never the engine's (ADR 0012).

WITH ordering AS (
    SELECT
        p.account_id,
        af.film_id,
        row_number() OVER (
            PARTITION BY p.account_id ORDER BY p.band DESC, p.rank ASC
        ) AS position,
        count(*) OVER (PARTITION BY p.account_id) AS films
    FROM placements p
    JOIN account_films af ON af.id = p.account_film_id
),
watches AS (
    SELECT
        w.account_id,
        w.film_id,
        CASE
            WHEN w.standing IN ('up_next', 'pool') THEN 'engine'
            ELSE 'owner'
        END AS source,
        (
            row_number() OVER (PARTITION BY w.account_id ORDER BY w.watched_at, w.id) - 1
        ) / (:watches_per_window)::integer AS window_index
    FROM watch_events w
    JOIN accounts a ON a.id = w.account_id
    WHERE NOT a.is_demo AND NOT w.rewatch
)
SELECT
    watches.account_id,
    watches.window_index,
    count(*) FILTER (
        WHERE watches.source = 'engine' AND o.film_id IS NOT NULL
    ) AS engine_landings,
    avg(
        (o.films - o.position + 1)::numeric / o.films
    ) FILTER (WHERE watches.source = 'engine') AS engine_landing,
    count(*) FILTER (
        WHERE watches.source = 'engine' AND o.film_id IS NULL
    ) AS engine_unplaced,
    count(*) FILTER (
        WHERE watches.source = 'owner' AND o.film_id IS NOT NULL
    ) AS owner_landings,
    avg(
        (o.films - o.position + 1)::numeric / o.films
    ) FILTER (WHERE watches.source = 'owner') AS owner_landing,
    count(*) FILTER (
        WHERE watches.source = 'owner' AND o.film_id IS NULL
    ) AS owner_unplaced
FROM watches
LEFT JOIN ordering o
    ON o.account_id = watches.account_id AND o.film_id = watches.film_id
GROUP BY watches.account_id, watches.window_index
ORDER BY watches.account_id, watches.window_index;
