# Screens and flows

Consolidates wayfinder ticket [Core flows in prose (#11)](https://github.com/KyleKDang/anchor/issues/11): the complete inventory of screens, flows, and interactions.
This doc fixes behavior and content, not visual design; implementation prototypes the look.
Surfacing and nudge moments are governed by [surfacing.md](surfacing.md); auth and account screens by [architecture.md](architecture.md).

## Top-level structure

Five top-level destinations: **Watchlist**, **Discovery**, **Rated**, **Search**, and **Profile**.
The film page is not a destination; it is reached by tapping a film anywhere.
Plot summaries sit behind the spoiler toggle on every surface that shows them.
Every surface carries its frequent verbs inline; the film page holds the complete set.

## Watchlist screen

One screen, two tiers: the ranked tier on top, the backlog below.
Before taste-profile readiness *ready* the screen is honestly just the backlog, with the explainer and progress nudge fixed in [onboarding-and-import.md](onboarding-and-import.md).

- Ranked-tier rows carry pin, veto, not-now, and mark-watched inline.
- Backlog rows carry mark-watched, pin, and veto inline.
- Backlog sorts: recently added (default), title, year; filters: genre, decade.
- **No engine-score sort, deliberately**: [ADR 0005](../adr/0005-no-rating-shaped-predictions.md) bars anything rating-shaped on unwatched films, and a score-ordered backlog would quietly become a second, undamped ranked tier.
- The vetoed list lives behind an overflow on this screen, reviewable and liftable.
- Tier changes land silently; no changelog affordance in v1 (damping keeps changes legible).

## Discovery screen

The ~20-film flat shelf as specified in [discovery.md](discovery.md), exemplar explanations visible by default.
Cards carry accept, dismiss, and seen-it inline.
The dismissed list lives behind an overflow on this screen.

## Rated screen

Default view: the ordering best to worst, grouped by band with the half-star value as the group header, anchors badged, tie-groups shown as one slot.

- A jump-to-band control and search-within-rated.
- Other sorts: recently rated (last placement or re-placement), recently watched (last watch event), title, release year.
  Any non-position sort drops the band grouping and shows a flat list.
- Filters: band range, genre, decade, has-open-drift-flag.
- A compact needs-attention strip at the top collects open drift flags and expands into the flagged-films list.
- A settling strip beside it carries the count of provisional films (anchors excluded) and the one button into settling; it is absent when nothing is provisional.
  Each provisional film's "settling" mark is the per-film door into the same flow.
- The rate-later queue is a secondary section here: watched-unrated films awaiting an optional placement.
- Band headers offer anchor management (pick this band's anchor from its films).

## Search screen

One dedicated screen searching TMDB.
The owner's own films are flagged inline when they match (already rated with its value, in backlog, watched-unrated).
Result rows carry add-to-backlog inline; everything else goes through the film page.

## Film page

Shifts with the film's state:

- **Not in your world**: metadata, spoiler-toggled plot, add-to-backlog, I-watched-this.
- **In backlog**: the same, plus pin, veto, remove-from-backlog; nothing rating-shaped, ever (ADR 0005).
- **Rated**: band and rating, position with immediate neighbors, anchor badge where it applies, watch history, judgment history (the film's comparison-log entries), log-a-rewatch, re-place ("settle it now" on a provisional film), make-anchor-of-its-band, and the open drift flag with its resolution options when one exists.
- **Watched-unrated**: seen marker plus place-it-now.

## Logging a watch

Mark-watched (from any row or the film page) offers **rate now** (primary) or **later** (quiet secondary).
Later converts the film to watched-unrated and it joins the rate-later queue.
Marking an already-rated film watched is the rewatch flow from [rating-system.md](rating-system.md): timestamp, optional re-placement, keeping the position is a confirming signal.

## Placement flow on screen

A full-screen guided flow, one comparison per step: the two films side by side with poster, title, and year; plot behind the spoiler toggle; all ratings hidden mid-flow so the pure which-is-better instinct answers, uncontaminated by the opponent's band.

- A / B / Tied / Skip per step, the sliver question when it applies, drift checks slipped in per [rating-system.md](rating-system.md).
- **Abandoning before the band locks**: the film becomes watched-unrated, joins the rate-later queue, and the answers already given stay in the append-only log and head-start the next attempt (the same trick re-placement uses).
- **Early bail after band lock** stays as decided: a provisional placement.
- **The placement-done screen** shows the landed rating and band and the film's immediate neighbors in the ordering, and hosts the optional criteria bonus question ([taste-profile.md](taste-profile.md)).
- **No undo button**; re-placement is the correction path.
- **Keep comparing**: the done screen offers extending the flow with further comparisons around the landed position, including band-edge anchor and sliver questions.
  Answers may move the film across a divider or move the divider itself; the doubt alone never moves anything.
  If the extended answers keep the film in place, the placement stands and the feeling was scale drift.

## Settling on screen

Settling is the placement flow run over provisional films one after another, entered from the Rated strip or from one film's own mark ([onboarding-and-import.md](onboarding-and-import.md) fixes what it does to the ordering).

- From the strip, the engine picks the next film: the one whose remaining range is narrowest, then the best-remembered by the warmup's candidate ranking.
  The flow carries a header naming the film and its progress ("Settling Heat - 3 of about 7"), and its landed screen offers next-film as the primary, with keep-comparing and the criteria bonus exactly as on any placement-done screen.
- A sitting is open-ended: no target, a leave control always visible, and a tally of the sitting (films settled, answers given) as the only thing it says about itself.
  Leaving mid-film is free, because the film's answers are already in the log and the next attempt resumes from them.
- "Not this one" moves on without a judgment and does not offer that film again in the sitting.
  Nothing is stored for it, since the next-film rule already puts barely-remembered films last.
- From one film's mark or page, the same flow runs for that film alone and lands on the ordinary done screen, with a quiet settle-another where more remain.
- Anchors are never offered; an anchor is re-placed only from its own page, with the warning from [rating-system.md](rating-system.md).
- Early bail stays available and lands provisionally as anywhere else; the tally counts graduations only.

## Anchor management

Two entry points, one flow: a rated film's page anchors that film's band (the designation-mismatch rule from [rating-system.md](rating-system.md) applies); a Rated band header opens the band's films to pick from.
Swapping simply retires the old anchor.

## Profile screen

- The prose taste profile: readable, corrected via structural constraints, never text edits.
- The quality picker, editable any time.
- The criteria-question frequency control and off switch.
- The readiness state shown honestly (cold / forming / ready and what each unlocks), linking into settling where comparisons are what is missing.
- The import / hard-reset entry with its enumerating type-to-confirm warning, and the unmatched-rows list under the import area.
- The Letterboxd area: the sync list and import residue counts ([surfacing.md](surfacing.md)).
- A small stats block: films rated, comparisons answered, the emergent distribution as a histogram.
- The mandatory TMDB attribution (logo and notice, per [ADR 0003](../adr/0003-tmdb-licensing-posture.md)).

## Little lists, contextual homes

| List | Home |
| --- | --- |
| Dismissed suggestions | behind an overflow on Discovery |
| Vetoed films | behind an overflow on Watchlist |
| Unmatched import rows | under Profile's import area |
| Rate-later queue | a section on Rated |
