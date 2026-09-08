-- Tier adoption: the tier-sourced share of logged watches.
--
-- Denominated in watches, because a watch is the only opportunity the ranked tier ever
-- gets: every time the owner sits down with a film, the tier either put it in front of
-- them or it did not.
--
-- A pinned film counts as the owner's pick and never the engine's (ADR 0012), so only the
-- up-next and pool standings are tier-sourced. The breakdown rides alongside the share
-- because the denominator is every logged watch there has ever been, imported diary
-- history included: an account that arrived with a thousand-film back catalogue reads low
-- for a long time, and the import column is what says why. Read the share against itself
-- over months, never against a number (evaluation.md: no targets anywhere).

SELECT
    a.id AS account_id,
    count(*) AS logged_watches,
    count(*) FILTER (WHERE w.standing IN ('up_next', 'pool')) AS tier_sourced_watches,
    count(*) FILTER (WHERE w.standing NOT IN ('up_next', 'pool')) AS other_watches,
    count(*) FILTER (WHERE w.standing = 'pinned') AS pinned_watches,
    count(*) FILTER (WHERE w.standing = 'plain_backlog') AS plain_backlog_watches,
    count(*) FILTER (WHERE w.origin = 'import_seeded') AS import_seeded_watches,
    count(*) FILTER (WHERE w.standing IN ('up_next', 'pool'))::numeric
        / nullif(count(*), 0) AS tier_adoption
FROM watch_events w
JOIN accounts a ON a.id = w.account_id
WHERE NOT a.is_demo
GROUP BY a.id
ORDER BY a.id;
