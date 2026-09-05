# Onboarding and the seed import

Consolidates wayfinder tickets [Seed import and onboarding design (#5)](https://github.com/KyleKDang/anchor/issues/5), [Obtain a real Letterboxd export (#16)](https://github.com/KyleKDang/anchor/issues/16), and [Fresh-account onboarding (#17)](https://github.com/KyleKDang/anchor/issues/17), as revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).
Vocabulary follows [CONTEXT.md](../../CONTEXT.md); the export's exact file inventory and CSV schemas are documented in [tmdb-letterboxd-data.md](../research/tmdb-letterboxd-data.md).

## The entry fork

Onboarding opens with an explicit fork: "Have a Letterboxd export?" leads into the seed import; "Start fresh" leads to the fresh bootstrap.
Both paths converge on the same warmup skeleton, and the import remains reachable later from settings.

## The seed import

### Seeding the ordering

Letterboxd's ten half-star values map 1:1 onto Anchor's bands, so every rated row lands in its band, rated, the moment it is matched.
Within a band the rows take the default order ([rating-system.md](rating-system.md)): TMDB average shrunk toward the catalog mean, best first.
Nothing about an imported film is provisional and nothing waits to be settled; the wall is complete when matching completes, and the owner reorders it in edit mode as much or as little as they like.
Imported ratings are the owner's own judgments and count as such everywhere: they train the taste profile at full band weight and carry the account past both readiness bars at once (below).

### The matching pipeline

Letterboxd rows carry name, year, and URI but no TMDB id, so import matches by title and year.

- **Auto-accept only unambiguous rows**: normalized title (NBSP, en-dash, and middle-dot folding) plus year with a plus/minus-1 retry yielding exactly one candidate, or an exact-title hit that dominates the runner-up on popularity.
- **Everything else queues to a review screen** showing poster, year, and director per candidate, ranked by popularity.
- **Rows that never match live in a persistent unmatched list**: the owner can manually search TMDB and bind, or dismiss the row for good; until then an unmatched row affects nothing.
- **The boxd.it-to-TMDB-id scrape survives only as a per-row "resolve via Letterboxd" rescue button** on the review screen: throttled, failure-tolerant, never bulk, never a pipeline dependency.

A real export (592 rows) confirmed the guessed ratings.csv and watchlist.csv schemas exactly and supplied matcher test cases: NBSP and en-dash in names, commas in titles, accented titles.
It contained no missing-year, TV-side, or deleted-film rows, so those paths and duplicate title+year cases require synthetic test fixtures; real exports may never exercise them.

### Re-import is a hard reset

Re-importing a fresh export is allowed, behind an explicit warning: it wipes all account data (ordering, anchors, comparison log, taste profile, backlog including hand-added films, watch history) and rebuilds from the new CSVs alone.
There is no merge path, ever; at most one seed import is in effect at a time.
The warning enumerates concretely what will be destroyed ("this erases 50 ratings, 12 anchors, 200 answers") and requires type-to-confirm when the ordering or the comparison log is non-trivial.
The comparison log's never-deleted rule has exactly this exception: account reset or deletion.

### The other CSVs

- **watchlist.csv** seeds the backlog through the same matching pipeline; rows already rated in the same import are skipped; unmatched rows join the same list.
- **watched.csv** rows without ratings become watched-unrated films: outside the ordering, the backlog, and the taste profile.
  Their only effects are the discovery-feed dedupe (never recommend a seen film) and a seat in the rate-later queue.
- **diary.csv** rows become internal watch events with rewatch flags; exported dates are New Zealand time, so a day of skew is possible.
  No diary UI follows.
- **profile.csv** is parsed for the Favorite Films column only; every other column is discarded unread (the rest is PII with no product use).

## Fresh-account bootstrap

A fresh ordering gets its first films through the band picker, one film at a time, and its first anchors by the owner marking the films they know cold as they rate them.
There is no separate designation flow: rate a film, mark it, and the band's pool exists.

- Candidate sourcing is hybrid: search is primary, with a TMDB popular/top-rated browse grid as an explicit "need inspiration?" fallback (popularity grids bias toward blockbusters, so search stays the headline act).
- Five whole-star bands are prompted, in ease-of-recall order (5, 1, 3, 4, 2); half-star bands are offered as an optional continuation, since "a definitive 3.5" is a harder judgment than "a definitive 3".
- Every prompt is individually skippable.

With no anchors and no films, the band picker shows empty pools and a pick is a plain pick.
The pools fill as the owner marks films, and the picker gets more useful with every one.

## The warmup

One shared skeleton with per-path fills, skippable at every point, the app fully usable throughout:

| Phase | Post-import | Fresh |
| --- | --- | --- |
| 1. Mark anchors | candidates per band, ranked by rewatch count then rating recency with TMDB popularity as tiebreak; profile favorites boosted to the top of their band's list; any number may be marked per band | search-driven with the browse fallback; five whole-star prompts, each film rated through the picker and marked |
| 2. Look over the wall | Rated opens in edit mode with a one-time explanation of dragging and marking; done once the owner has moved a few films, or skipped | "rate ~5 films you've seen": normal placements |
| 3. Seed the backlog | watchlist.csv | "add films you've been meaning to watch": search, plus discovery once live |

Marking remains the owner's act in both fills; the app never marks an anchor itself.
The ~5-film target is advisory, a tuning knob: it leaves a fresh ordering of about ten films including the anchors, enough for the picker's pools to mean something.

## Feature light-up

- **Backlog**: usable from minute one.
- **Discovery feed**: at readiness *forming*.
- **Ranked tier**: at readiness *ready*.
  Pre-gate, the watchlist shows the plain backlog, honestly unranked, with an explainer and a progress line toward unlocking, counted in films rated.
  A fake popularity-ranked tier would teach the owner on day one that the tier's opinion is worthless.
- **A seed import crosses both bars at once** whenever it holds enough rated films, which any real export does: the moment matching completes, discovery and the ranked tier are live.
  That is the point of importing.
  A fresh account earns them the same way, one placement at a time, and both gates are honest: they activate on evidence and never fabricate from zero signal.

The readiness states themselves are defined in [taste-profile.md](taste-profile.md); the pre-gate screen states are in [screens-and-flows.md](screens-and-flows.md) and the unlock moments in [surfacing.md](surfacing.md).
