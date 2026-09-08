-- Tier adoption: the tier-sourced share of logged watches.
--
-- Denominated in watches, because a watch is the only opportunity the ranked tier ever
-- gets: every time the owner sits down with a film, the tier either put it in front of
-- them or it did not.
--
-- A pinned film counts as the owner's pick and never the engine's (ADR 0012), so only the
-- up-next and pool standings are tier-sourced.
--
-- Watches that arrived with a seed import are not opportunities and are set aside. They
-- carry their real diary date, so they sit before the account existed, and the tier could
-- no more have sourced them than it could have been in the room. They stay visible as
-- `watches_before_the_account`, because a denominator that quietly drops a thousand rows
-- is worse than one that shows what it dropped. A diary row that arrived without a date
-- is stamped on import and so counts here: a small residue, and the only one.
--
-- Read the share against itself over months, never against a number.

SELECT
    a.id AS account_id,
    count(w.id) FILTER (WHERE w.watched_at > a.created_at) AS logged_watches,
    count(w.id) FILTER (WHERE w.watched_at <= a.created_at) AS watches_before_the_account,
    count(w.id) FILTER (
        WHERE w.watched_at > a.created_at AND w.standing IN ('up_next', 'pool')
    ) AS tier_sourced_watches,
    count(w.id) FILTER (
        WHERE w.watched_at > a.created_at AND w.standing NOT IN ('up_next', 'pool')
    ) AS other_watches,
    count(w.id) FILTER (
        WHERE w.watched_at > a.created_at AND w.standing = 'pinned'
    ) AS pinned_watches,
    count(w.id) FILTER (
        WHERE w.watched_at > a.created_at AND w.standing = 'plain_backlog'
    ) AS plain_backlog_watches,
    count(w.id) FILTER (
        WHERE w.watched_at > a.created_at AND w.standing IN ('up_next', 'pool')
    )::numeric
        / nullif(count(w.id) FILTER (WHERE w.watched_at > a.created_at), 0) AS tier_adoption
FROM accounts a
LEFT JOIN watch_events w ON w.account_id = a.id
WHERE NOT a.is_demo
GROUP BY a.id
ORDER BY a.id;
