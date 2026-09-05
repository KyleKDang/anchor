# The rating system

Consolidates wayfinder tickets [Ordering and comparison mechanics (#4)](https://github.com/KyleKDang/anchor/issues/4) and [Drift, re-rating, and rewatch design (#6)](https://github.com/KyleKDang/anchor/issues/6), as revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).
Vocabulary follows [CONTEXT.md](../../CONTEXT.md).

## The ordering

The ordering is ten band rows, best band first, each a strict order of the films in it.
It is explicit, persisted state ([ADR 0001](../adr/0001-explicit-ordering-not-model-derived.md)): every film's band and rank are written down, and nothing derives them.
Nothing in it moves except through the owner's own acts - a pick on the band picker, a move on the wall, a re-rate - and the engine can never reorder it.

There are no ties.
Two films the owner cannot separate still sit one above the other, and the engine is what absorbs that: it reads within-band order as a range, not a verdict between neighbours.
The tie-group of the earlier design answered a real difficulty, that many pairs are genuinely undecidable, but it swallowed whole imports and left the ordering invisible; a strict order the engine reads coarsely keeps the honesty without the cost.

### Default order

A film that has not been moved holds the rank the default order gave it: within a band, TMDB average shrunk toward the catalog mean where votes are few, best first, title as the tiebreak.
The shrinkage exists so an obscure film with a handful of perfect votes does not top a row.
The same rule seeds every imported band and seats every newly rated film, so there is one rule to know.
Default order is a starting point, never a judgment: it is where a film waits for the owner.

### What within-band order means

To the owner, rank is exact: a film is above the one below it because they put it there.
To the engine, within-band order is coarse: a film near the top of its band is preferred to one near the bottom, and two neighbours are near enough to equal.
The band is the strong judgment; rank inside it is a range that sharpens with distance.
The pair rules are fixed in [taste-profile.md](taste-profile.md).

## Bands and anchors

Bands are the ten half-star values, and a film's band is its rating: chosen by the owner, stored on the placement, shown as plain half-stars.
Above the best 5.0 is still 5.0; below the worst 0.5 is still 0.5.

An anchor is a film the owner has marked as one they are certain of: a definitive 5.0, a definitive 3.5 ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).
Any number per band; a band's anchors are its anchor pool.
The pool is what the band picker shows for the band and what its comparisons draw on, so the anchors are the owner's own scale made visible.

An anchor sits wherever it sits in its band.
The pool of the 5.0 band is naturally the very best films the owner has seen; the pool of the 3.0 band is its typical members.
So an anchor is a bound, never a floor or a ceiling: losing to every 5.0 anchor does not make a film a 4.5, it makes it at most a 5.0, and the seam between two bands is settled by the boundary films, not by the anchors.

- **Mark**: any rated film, from its page, from its poster in edit mode, or in the warmup.
  The app may suggest candidates but never marks one itself.
- **Retire**: the same toggle, off.
  Changes nothing else.
- **A move across bands retires**: an anchor dragged or re-rated into another band loses its mark visibly, because a reference that moved is no longer certain.
  Re-marking it in the new band is one tap.
- **Anchors are films like any other on the wall**: they move by drag inside their band and count in every training pair.

## The band picker

The band picker is the whole of rating a film.
It shows the ten bands, each with its anchor pool, and the owner does one of two things.

**Pick one band.**
The film is rated, no questions asked.
With the pools on screen the pick has already been made against the owner's own references, and a check after every rating would be the chore this design removed.

**Select a range.**
An owner unsure between two or three adjacent bands selects them, and comparisons narrow the range to one band, each drawn from the anchor pools of the bands still in it.

### Narrowing a range

Each question sets the film against one film standing for a band in the range and asks: better, worse, or about the same.
Answers bound the film: better than a film of band B means at least B, worse means at most B, about the same means B and ends the search.
Skip records nothing and swaps in the band's next candidate.

Which film is asked about is the advisory choice, and the rule is the question most likely to end the search:

- With three bands in the range, an anchor of the middle band, the one nearest the middle of its pool: either direction drops a band.
- With two bands, the weakest anchor of the upper band, since beating it settles the upper band; then the strongest anchor of the lower band, since losing to it settles the lower.
- When those come back the unhelpful way - the film loses to the upper band's weakest anchor and beats the lower band's strongest - it sits at the seam, and anchors cannot settle it, because an anchor bounds and never floors: losing to every 5.0 anchor leaves a low 5.0 exactly as possible as a high 4.5.
  The boundary question settles it: the bottom film of the upper band and the top film of the lower, side by side, and the owner says which the film is closer to.
  That is a band pick with two exemplars, and it lands the film beside the film it was judged closer to.
- A band with no anchor is stood for by a stand-in: for the middle band its middle film, for an outer band the film nearest the seam.
  A band with no film at all cannot be asked about, and when nothing is left to ask, the owner picks from what remains of the range.

A range costs one to four answers.

### Landing

A rated film lands in its band at its default rank, clipped to what its comparisons proved: above every film it beat, below every film it lost to, beside the film it was judged closer to, so a landing never contradicts an answer just given.
Then the wall opens in edit mode with the film highlighted, and the owner drags it to where it belongs or leaves it.
The on-screen shape, abandonment, and the done screen are in [screens-and-flows.md](screens-and-flows.md).

## Moves

A move is the owner dragging a film to a new rank or a new band in edit mode.
Every drop saves at once; there is nothing to submit and nothing to undo but the next drag.
A move within a band changes only ranks.
A move across bands changes the film's rating, retires it as an anchor if it was one, and is the whole of how a rating gets corrected: the owner sees the wall, disagrees with it, and puts the film where it goes.
The app never suggests a move, never queues one, and never performs one.

## Re-rating and rewatches

Re-rating is the band picker run again for a rated film, opened from a rewatch or from the film's own page.
Landing in the same band keeps the film's rank.
Landing in another takes the default rank there, retires the anchor mark if the film had one, and opens the wall as a placement does.

A rewatch is offered, never forced.
It timestamps the watch and asks one light question: still feel the same?
Confirming keeps everything as it is and is recorded as such; changing your mind opens the picker with the current band marked.

## The comparison log

An append-only log records every judgment; nothing is ever edited or deleted (the account-realm wipe is the sole exception).

- Band comparisons carry the film being rated, the anchor or stand-in, the verdict (better, worse, about the same, skipped), the range they were narrowing, the context (placement or re-rate), and a timestamp.
- Band picks carry the film and the band chosen, whether picked outright, at the boundary question (the two exemplars named), or as the last resort of a range.
- Criteria answers carry the pair, the quality, and the verdict, including offers ignored or dismissed.
- No entry has a status.
  A judgment the ordering has since been moved past is not flagged or superseded; whoever reads it reads it against the ordering as it stands, and the ordering wins ([ADR 0010](../adr/0010-comparison-log-is-evidence-not-event-source.md): the log is evidence, not an event source, and the ordering is primary state).
- No expiry or decay exists at this layer.

## Hard walls

- Criteria questions ("which had the better screenplay?") feed only the taste profile and never move the ordering; a criteria answer cannot decide overall betterness ([ADR 0007](../adr/0007-criteria-answers-are-evidence-not-orderings.md)).
  The criteria system is specified in [taste-profile.md](taste-profile.md).
- Band comparisons bound a landing and decide nothing else: they never move an anchor, never move another film, and never re-rank anything already on the wall.
- The engine never writes the ordering: not a rank, not a band, not a suggestion of either ([ADR 0001](../adr/0001-explicit-ordering-not-model-derived.md)).
- No prediction is ever shown for an unwatched film ([ADR 0005](../adr/0005-no-rating-shaped-predictions.md)); a predicted band would steer a pick and a move alike.
