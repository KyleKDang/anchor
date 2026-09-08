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
-- Only the accept door is counted. A seen-it is stamped hand-added on purpose, and a film
-- the owner hand-added after seeing it on the shelf is not something discovery caused.

SELECT
    a.id AS account_id,
    count(af.id) AS accepted,
    count(af.id) FILTER (
        WHERE af.state IN ('watched_unrated', 'rated')
    ) AS watched,
    count(af.id) FILTER (WHERE af.state = 'rated') AS rated,
    count(af.id) FILTER (
        WHERE af.state IN ('watched_unrated', 'rated')
    )::numeric / nullif(count(af.id), 0) AS watched_share,
    count(af.id) FILTER (WHERE af.state = 'rated')::numeric
        / nullif(count(af.id), 0) AS rated_share
FROM accounts a
LEFT JOIN account_films af
    ON af.account_id = a.id AND af.origin = 'discovery_accept'
WHERE NOT a.is_demo
GROUP BY a.id
ORDER BY a.id;
