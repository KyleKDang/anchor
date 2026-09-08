-- The discovery funnel: accepted, then watched, then rated.
--
-- Denominated in accepts, which is the opportunity each later stage had. It answers the
-- question a dismissal rate cannot: the owner said yes, and then what - did the film get
-- watched at all, and once watched did they care enough to place it? A feed that is
-- accepted from and never watched is being agreed with rather than used.
--
-- The shares are cumulative against the accepts, not stage to stage, so the three read as
-- one funnel. Both later stages are read off the film's lifecycle state as it now stands,
-- so a film watched or rated a year after it was accepted completes the fact then.
--
-- These accepts are the surviving account-films rather than the counter that
-- discovery_rates.sql reads, because the funnel is about what became of the film and a
-- film with no row has no state to ask about. The cost is that an accepted film the owner
-- later took back out of the backlog leaves the funnel entirely rather than sitting in it
-- as an accept that went nowhere, which flatters the shares. Read `accepted` here against
-- `accepts` there: the gap is exactly the films that were taken back.
--
-- Only the accept door is counted. A seen-it is stamped hand-added on purpose, and a film
-- the owner hand-added after seeing it on the shelf is not something discovery caused.

WITH funnel AS (
    SELECT
        a.id AS account_id,
        count(af.id) AS accepted,
        count(af.id) FILTER (WHERE af.state IN ('watched_unrated', 'rated')) AS watched,
        count(af.id) FILTER (WHERE af.state = 'rated') AS rated
    FROM accounts a
    LEFT JOIN account_films af
        ON af.account_id = a.id AND af.origin = 'discovery_accept'
    WHERE NOT a.is_demo
    GROUP BY a.id
)
SELECT
    account_id,
    accepted,
    watched,
    rated,
    watched::numeric / nullif(accepted, 0) AS watched_share,
    rated::numeric / nullif(accepted, 0) AS rated_share
FROM funnel
ORDER BY account_id;
