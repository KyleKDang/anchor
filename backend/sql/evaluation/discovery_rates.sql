-- Accept rate and dismissal rate, per restock.
--
-- A restock is the opportunity: it is the moment the feed sources candidates, judges them
-- and puts a shelf in front of the owner. Rates per week would say nothing here, because
-- a restock only runs for an owner who has opened the feed since the last one - an owner
-- who ignores discovery for a month has been offered nothing and has turned nothing down.
--
-- Accepts are counted off the account-film's origin stamp, which is written once, at the
-- moment the card was accepted, and read at watch time by nothing else. A seen-it is
-- deliberately absent from both numerators: it is stamped hand-added, because the owner
-- had watched the film before Anchor ever mentioned it (discovery.md).
--
-- Dismissals count films the owner has ever ruled out. Lifting stamps rather than deletes,
-- so a lifted dismissal stays counted - it happened - and `standing_dismissals` is what
-- the feed is currently suppressing.

SELECT
    a.id AS account_id,
    coalesce(f.restock_counter, 0) AS restocks,
    coalesce(accepted.accepts, 0) AS accepts,
    coalesce(turned_down.dismissals, 0) AS dismissals,
    coalesce(turned_down.standing_dismissals, 0) AS standing_dismissals,
    coalesce(accepted.accepts, 0)::numeric
        / nullif(f.restock_counter, 0) AS accepts_per_restock,
    coalesce(turned_down.dismissals, 0)::numeric
        / nullif(f.restock_counter, 0) AS dismissals_per_restock
FROM accounts a
LEFT JOIN feed_states f ON f.account_id = a.id
LEFT JOIN (
    SELECT account_id, count(*) AS accepts
    FROM account_films
    WHERE origin = 'discovery_accept'
    GROUP BY account_id
) accepted ON accepted.account_id = a.id
LEFT JOIN (
    SELECT
        account_id,
        count(*) AS dismissals,
        count(*) FILTER (WHERE lifted_at IS NULL) AS standing_dismissals
    FROM dismissals
    GROUP BY account_id
) turned_down ON turned_down.account_id = a.id
WHERE NOT a.is_demo
ORDER BY a.id;
