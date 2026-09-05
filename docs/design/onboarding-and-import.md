# Onboarding and the seed import

Consolidates wayfinder tickets [Seed import and onboarding design (#5)](https://github.com/KyleKDang/anchor/issues/5), [Obtain a real Letterboxd export (#16)](https://github.com/KyleKDang/anchor/issues/16), and [Fresh-account onboarding (#17)](https://github.com/KyleKDang/anchor/issues/17).
Vocabulary follows [CONTEXT.md](../../CONTEXT.md); the export's exact file inventory and CSV schemas are documented in [tmdb-letterboxd-data.md](../research/tmdb-letterboxd-data.md).

## The entry fork

Onboarding opens with an explicit fork: "Have a Letterboxd export?" leads into the seed import; "Start fresh" leads to the fresh bootstrap.
Both paths converge on the same warmup skeleton, and the import remains reachable later from settings.

## The seed import

### Seeding the ordering

Letterboxd's ten half-star values map 1:1 onto Anchor's bands.
Each value becomes one provisional tie-group in the ordering; no within-band order is ever fabricated.
Imported ratings count as the owner's band judgments: they pin all nine dividers at import, at lower weight than live answers, so imported films display their familiar half-star ratings immediately while a handful of fresh judgments can move a divider against hundreds of stale seeds.
Bands with no imported films simply have unpinned dividers until judgments accumulate; the no-anchor mechanics in [rating-system.md](rating-system.md) cover them.

### The matching pipeline

Letterboxd rows carry name, year, and URI but no TMDB id, so import matches by title and year.

- **Auto-accept only unambiguous rows**: normalized title (NBSP, en-dash, and middle-dot folding) plus year with a plus/minus-1 retry yielding exactly one candidate, or an exact-title hit that dominates the runner-up on popularity.
- **Everything else queues to a review screen** showing poster, year, and director per candidate, ranked by popularity.
- **Rows that never match live in a persistent unmatched list**: the owner can manually search TMDB and bind, or dismiss the row for good; until then an unmatched row affects nothing.
- **The boxd.it-to-TMDB-id scrape survives only as a per-row "resolve via Letterboxd" rescue button** on the review screen: throttled, failure-tolerant, never bulk, never a pipeline dependency.

A real export (592 rows) confirmed the guessed ratings.csv and watchlist.csv schemas exactly and supplied matcher test cases: NBSP and en-dash in names, commas in titles, accented titles.
It contained no missing-year, TV-side, or deleted-film rows, so those paths and duplicate title+year cases require synthetic test fixtures; real exports may never exercise them.

### Re-import is a hard reset

Re-importing a fresh export is allowed, behind an explicit warning: it wipes all account data (ordering, comparison log, anchors, drift flags, taste profile, backlog including hand-added films, watch history) and rebuilds from the new CSVs alone.
There is no merge path, ever; at most one seed import is in effect at a time.
The warning enumerates concretely what will be destroyed ("this erases 50 ratings, 200 comparisons, 3 anchors") and requires type-to-confirm when the comparison log is non-trivial.
The comparison log's never-deleted rule has exactly this exception: account reset or deletion.

### The other CSVs

- **watchlist.csv** seeds the backlog through the same matching pipeline; rows already rated in the same import are skipped; unmatched rows join the same list.
- **watched.csv** rows without ratings become watched-unrated films: outside the ordering, the backlog, and the taste profile.
  Their only effects are the discovery-feed dedupe (never recommend a seen film) and a seat in the rate-later queue.
- **diary.csv** rows become internal watch events with rewatch flags; exported dates are New Zealand time, so a day of skew is possible.
  No diary UI follows.
- **profile.csv** is parsed for the Favorite Films column only; every other column is discarded unread (the rest is PII with no product use).

## Fresh-account bootstrap

A fresh ordering gets its first structure from anchor designation, the one sanctioned direct band assignment: the owner searches for films they know cold and designates band exemplars, which simultaneously places those films and erects the first dividers.

- Candidate sourcing is hybrid: search is primary, with a TMDB popular/top-rated browse grid as an explicit "need inspiration?" fallback (popularity grids bias toward blockbusters, so search stays the headline act).
- Five whole-star bands are prompted, in ease-of-recall order (5, 1, 3, 4, 2); half-star bands are offered as an optional continuation, since "a definitive 3.5" is a harder judgment than "a definitive 3".
- Every prompt is individually skippable.

Ballpark guesses stay pure search seeds with zero evidentiary weight; they are never elevated to band judgments.
The seed-import elevation is justified by imported ratings being real historical judgments; a mid-log hunch pinning dividers would quietly reintroduce the drifting absolute scale.
Films placed before any band structure exists display position-only, rating pending: the ordering shows, and half-stars materialize account-wide as anchors and band evidence accumulate.

## The warmup

One shared skeleton with per-path fills, skippable at every point, the app fully usable throughout:

| Phase | Post-import | Fresh |
| --- | --- | --- |
| 1. Designate anchors | candidates per band, ranked by rewatch count then rating recency with TMDB popularity as tiebreak; profile favorites boosted to the top of their band's list | search-driven with the browse fallback; five whole-star prompts |
| 2. Gather evidence | "settle a few films": settling, counted as ~10 comparisons answered since the import | "log ~5 films you've seen": normal placements against the anchors |
| 3. Seed the backlog | watchlist.csv | "add films you've been meaning to watch": search, plus discovery once live |

Designation remains the owner's act in both fills; the app never self-designates.
The ~5-film target is advisory, a tuning knob: at a handful of comparisons per placement it roughly matches the import warmup's evidence volume and leaves an ordering of about ten films including the anchors.

## The provisional lifecycle

Provisional status ends when the advisory math's placement confidence crosses the same threshold a normal placement needs: one unified rule for seed-import and early-bail placements.
Provisional films serve as comparison opponents (post-import there is no alternative); each such comparison doubles as the seed film's first real evidence, pulling it out of its tie-group, and the math prefers confident pivots as they accumulate.
A Tied answer against a provisional film pulls that film out into a definitive two-film tie-group at the landing point; provisional membership is never inherited.

Settling is the owner's own door, for the owner who has nothing left to log: it runs the placement flow over provisional films one after another, each search head-started by every judgment the film has collected as an opponent, so a film others have already been compared against is a question or two from graduating.
The film offered next is the one with the narrowest remaining range, then the best-remembered by the warmup's candidate ranking, because a settled film the owner remembers cold becomes a confident pivot for every later placement and a barely-remembered one produces skips.
Anchors are never offered.
The on-screen shape is in [screens-and-flows.md](screens-and-flows.md); the door's loudness is fixed in [surfacing.md](surfacing.md).

## Feature light-up

- **Backlog**: usable from minute one.
- **Ranked tier**: gated behind taste-profile readiness *ready*.
  Pre-gate, the watchlist shows the plain backlog, honestly unranked, with an explainer and a progress nudge toward unlocking.
  A fake popularity-ranked tier would teach the owner on day one that the tier's opinion is worthless.
- **Discovery feed**: lights up earlier, at readiness *forming* (a seed import lands there immediately).
  Anchor designations are the densest per-click taste signal the account ever emits, and discovery doubles as the backlog filler a fresh account needs.
  Same honesty rule: it activates on readiness, never fabricates from zero signal.

The readiness states themselves are defined in [taste-profile.md](taste-profile.md); the pre-gate screen states are in [screens-and-flows.md](screens-and-flows.md) and the unlock moments in [surfacing.md](surfacing.md).
