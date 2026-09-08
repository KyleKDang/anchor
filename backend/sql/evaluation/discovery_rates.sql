-- Accept rate and dismissal rate, per restock.
--
-- A restock is the opportunity: it is the moment the feed sources candidates, judges them
-- and puts a shelf in front of the owner. Rates per week would say nothing here, because
-- a restock only runs for an owner who has opened the feed since the last one - an owner
-- who ignores discovery for a month has been offered nothing and has turned nothing down.
--
-- All three numbers are counters on the feed's own row, started on the same day, so both
-- rates cover one span on both sides. Neither answer is countable from what it wrote: an
-- accept writes an account-film that removing the film from the backlog deletes outright,
-- which would quietly drop exactly the accepts the owner thought better of; and while a
-- dismissal does leave a durable row, those rows run from the account's first day while
-- the restock counter runs from the day it was added.
--
-- A seen-it is deliberately in neither numerator: it is stamped hand-added, because the
-- owner had watched the film before Anchor ever mentioned it (discovery.md).
--
-- Turning a film down again after lifting it counts as the second answer it was.

SELECT
    a.id AS account_id,
    coalesce(f.restock_counter, 0) AS restocks,
    coalesce(f.accept_counter, 0) AS accepts,
    coalesce(f.dismissal_counter, 0) AS dismissals,
    coalesce(f.accept_counter, 0)::numeric
        / nullif(f.restock_counter, 0) AS accepts_per_restock,
    coalesce(f.dismissal_counter, 0)::numeric
        / nullif(f.restock_counter, 0) AS dismissals_per_restock
FROM accounts a
LEFT JOIN feed_states f ON f.account_id = a.id
WHERE NOT a.is_demo
ORDER BY a.id;
