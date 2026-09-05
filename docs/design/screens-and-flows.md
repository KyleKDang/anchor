# Screens and flows

Consolidates wayfinder ticket [Core flows in prose (#11)](https://github.com/KyleKDang/anchor/issues/11): the complete inventory of screens, flows, and interactions, as revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).
This doc fixes behavior and content, not visual design; the look is fixed in [visual-design.md](visual-design.md).
Surfacing and nudge moments are governed by [surfacing.md](surfacing.md); auth and account screens by [architecture.md](architecture.md).

## Top-level structure

Five top-level destinations: **Watchlist**, **Discovery**, **Rated**, **Search**, and **Profile**.
The film page is not a destination; it is reached by tapping a film anywhere.
Plot summaries sit behind the spoiler toggle on every surface that shows them.
Every surface carries its frequent verbs inline; the film page holds the complete set.

## Watchlist screen

One screen, two tiers: the ranked tier on top, the backlog below.
Before taste-profile readiness *ready* the screen is honestly just the backlog, with the explainer and progress line fixed in [onboarding-and-import.md](onboarding-and-import.md).

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

The ordering as a wall of posters in ten band rows, best band first, the half-star value as each row's header and the rank stamped on every poster; anchors badged.
The wall's visual rules are in [visual-design.md](visual-design.md).

- A jump-to-band control and search-within-rated.
- Other sorts: recently rated (last placement or re-rate), recently watched (last watch event), title, release year.
  Any non-position sort drops the band rows, shows a flat list, and hides the edit toggle.
- Filters: band range, genre, decade, anchors only.
- Each band header carries the count of the band's anchors.
- The rate-later queue is a secondary section here: watched-unrated films awaiting an optional placement.

### Edit mode

One toggle turns the wall into the editor; the demo account never has it.

- Every poster drags, within its band and into any other, and the row it is over opens a gap where it would land.
  Dropping saves at once: there is no save button and no draft state, and the visible result is the confirmation.
- A poster can also be selected and moved with the keyboard: arrows move it one rank, with a modifier to the ends of its band, and up and down across bands, so a long row never needs a long drag.
- Each poster carries the anchor toggle; marking is one tap, and the badge is the confirmation.
- Filters stay usable in edit mode.
  A drop between two visible films lands the film directly after the upper one, whatever is hidden between them, so filtering a big band down to the films the owner is thinking about is the intended way to work a long row.
- A film moved across bands loses its anchor badge as it lands; nothing else about the drop is announced.
- While the account has no anchors, one ambient line in edit mode says what marking one does; it vanishes at the first anchor.
- The wall auto-scrolls while a drag nears an edge, and Escape cancels a drag in progress.

Leaving edit mode is the same toggle.

## Search screen

One dedicated screen searching TMDB.
The owner's own films are flagged inline when they match (already rated with its value, in backlog, watched-unrated).
Result rows carry add-to-backlog inline; everything else goes through the film page.

## Film page

Shifts with the film's state:

- **Not in your world**: metadata, spoiler-toggled plot, add-to-backlog, I-watched-this.
- **In backlog**: the same, plus pin, veto, remove-from-backlog; nothing rating-shaped, ever (ADR 0005).
- **Rated**: band and rating, rank within the band with the immediate neighbours above and below, the anchor toggle, watch history, judgment history (the film's comparison-log entries), log-a-rewatch, re-rate, and "answer questions about this film", which opens a criteria session.
- **Watched-unrated**: seen marker plus rate-it-now.

## Logging a watch

Mark-watched (from any row or the film page) offers **rate now** (primary) or **later** (quiet secondary).
Later converts the film to watched-unrated and it joins the rate-later queue.
Marking an already-rated film watched is the rewatch flow from [rating-system.md](rating-system.md): timestamp, still-feel-the-same, and an optional re-rate; keeping the rating is a confirming signal.

## The band picker on screen

A full-screen flow.
Ten bands as rows, best first, each row showing its anchor pool as small posters (up to a handful, with a count where the pool is larger) so the owner is choosing against their own references; a band with no anchors shows its label alone.
The film being rated sits at the top with poster, title, year, and the plot behind the spoiler toggle.

- **Tap a band** and the film is rated: it lands at its default rank and the done screen follows.
- **Select a range** by tapping two or three adjacent bands, then confirm; the comparisons begin.
- **A comparison step** shows the film and its opponent side by side with poster, title, and year, the opponent labelled with its band, since the band is what the question is about.
  Answers: better, worse, about the same, skip.
  The boundary question shows the two boundary films with their bands and asks which the film is closer to.
  The last-resort pick shows the bands still in the range.
- **Abandoning mid-range**: the film becomes watched-unrated, joins the rate-later queue, and the answers already given stay in the log; the next attempt starts the picker fresh.
- **The done screen** shows the landed band and rank with the film's immediate neighbours, and offers two things: "Adjust on the wall" as the primary, which opens Rated in edit mode with the film highlighted, and the criteria run (below).
  It carries the readiness unlock line when this very placement crossed a bar.
- **No undo button**; the wall is the correction path.

Re-rating opens the same picker with the film's current band marked and its current rank shown; the done screen is the same.

## Criteria questions on screen

Two homes, one card.
The card shows the two films side by side, the question ("Which had the better screenplay?"), the two films as answers, "about the same", and a small dismiss.

- **The run on the done screen**: when the frequency setting allows it, a card sits under the landing.
  Answering it slides the next card in; dismissing or leaving ends the run.
  It never blocks the primary action, and ignoring it costs nothing.
- **The session from a film page**: "answer questions about this film" opens a full-screen stream of cards about that one film against varied opponents, with a leave control always visible and a count of answers given as the only thing it says about itself.
  It is open-ended: it runs until the owner leaves or the app has nothing left to ask that it has not asked before.

## Anchor management

Three entry points, one toggle: a rated film's page, its poster in edit mode, and the warmup's mark-anchors step.
Marking and retiring are the same control, and a move across bands retires on its own.

## Profile screen

- The prose taste profile: readable, corrected via structural constraints, never text edits.
- The quality picker, editable any time.
- The criteria-question frequency control and off switch, governing the done-screen run only.
- The readiness state shown honestly (cold / forming / ready and what each unlocks), counted in films rated and bands spanned.
- The import / hard-reset entry with its enumerating type-to-confirm warning, and the unmatched-rows list under the import area.
- The Letterboxd area: the sync list and import residue counts ([surfacing.md](surfacing.md)).
- A small stats block: films rated, anchors marked, answers given, the emergent distribution as a histogram.
- The mandatory TMDB attribution (logo and notice, per [ADR 0003](../adr/0003-tmdb-licensing-posture.md)).

## Little lists, contextual homes

| List | Home |
| --- | --- |
| Dismissed suggestions | behind an overflow on Discovery |
| Vetoed films | behind an overflow on Watchlist |
| Unmatched import rows | under Profile's import area |
| Rate-later queue | a section on Rated |
