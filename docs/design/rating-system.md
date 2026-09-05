# The rating system

Consolidates wayfinder tickets [Ordering and comparison mechanics (#4)](https://github.com/KyleKDang/anchor/issues/4) and [Drift, re-rating, and rewatch design (#6)](https://github.com/KyleKDang/anchor/issues/6).
Vocabulary follows [CONTEXT.md](../../CONTEXT.md).

## The ordering

The ordering is an explicit, persisted sequence of tie-group slots over all rated films.
Every pair of films is comparable; ties are allowed and definitive, so the layer is a sequence of ordered tie-groups rather than a strict total order.

Probabilistic scoring math runs in the background as advisory only ([ADR 0001](../adr/0001-explicit-ordering-not-model-derived.md)).
It selects informative comparison opponents, judges placement confidence, and raises drift flags.
It never moves anything: nothing in the ordering moves except through the owner's judgments or explicit actions.

## Anchors, bands, and dividers

An anchor is the canonical exemplar of its band ("this film is what a 5 is"): at most one per band, up to ten across the ten half-star bands ([ADR 0002](../adr/0002-anchors-are-centroids-with-derived-dividers.md)).
Anchors are centroids, not boundaries; the boundary between two adjacent bands is a divider, a derived marker sitting between the two anchors.
Dividers are pinned by the owner's band judgments and move as the owner's sense of the band evolves; they are never set directly.

A film's rating is which dividers its position sits between, displayed as plain half-stars.
Above the canonical 5.0 is still 5.0; below the canonical 0.5 is still 0.5.
Comparisons cannot move anchors; only the owner can, through explicit re-placement.

Bands with no anchor work fully, via a fallback ladder: the film's position decides the band when unambiguous; otherwise a sliver question against stand-in exemplars (the most confidently-placed films near each candidate band's middle); otherwise a plain band pick.

## The placement flow

Placement finds a new film's slot in the ordering through comparisons.

- The owner may give an optional ballpark guess (a half-star value or range) when logging a film.
  The guess seeds the search at the nearest anchor but never sets the rating; comparisons always win.
- The search walks bands until the band locks, then bisects within the band, preferring confidently-placed pivots.
- Every question offers four answers: A, B, Tied, and Skip.
  Tied joins the film to the opponent's tie-group and ends the search, definitively.
  Skip records no judgment and swaps in another opponent; it exists so a barely-remembered film never forces a junk judgment.
- A sliver question ("closer in quality to canonical 4.0 or canonical 4.5?") is asked only when a film lands between the highest known member of one band and the lowest known member of the next.
  The answer places the film and sharpens the divider.
- Three endings: an exact slot; a tie; or an early bail once the band is locked, which yields a provisional placement mid-band (the same trusted-less status as seed-import placements).
- Typical engaged placement: about 4-7 answers.
  Lazy placement: 2-3, with the visible star rating settled in the first ~2.

The on-screen flow, abandonment behavior, and the keep-comparing extension are specified in [screens-and-flows.md](screens-and-flows.md); mechanically, keep-comparing is simply further comparisons around the landed position, and only the answers can move the film or a divider.

## Hard walls

- Criteria questions ("which had the better screenplay?") feed only the taste profile and never move the ordering; a criteria answer cannot decide overall betterness ([ADR 0007](../adr/0007-criteria-answers-are-evidence-not-orderings.md)).
  Overall verdicts feed both the ordering and the taste profile.
  The app may piggyback one optional criteria question after a placement, about the just-judged pair; the full criteria system is specified in [taste-profile.md](taste-profile.md).
- Contradictions never auto-reorder.
  A judgment against the current ordering is stored, marked in tension, and raises a drift flag on whichever involved film the advisory math trusts least; the owner resolves.

## The comparison log

An append-only log records every judgment; nothing is ever deleted (the account-realm wipe is the sole exception).

- Each record carries the two films, the verdict (A / B / tied), a timestamp, the context that produced it (placement of X, drift check, keep-comparing, warmup, re-placement, spontaneous), and a status: active, in tension (contradicting the ordering), or superseded (settled against by an owner resolution).
- Skips, sliver answers, and criteria answers ride in the same log as typed sibling records.
- No expiry or decay exists at this layer: recency weighting is the taste model's business, and changing taste is drift's business.
- The log is evidence, not an event source ([ADR 0010](../adr/0010-comparison-log-is-evidence-not-event-source.md)): the ordering, dividers, and designations are primary state, and replaying the log is not guaranteed to reproduce them.

## Drift

Drift is the condition where later judgments contradict a film's position in the ordering.
A displayed rating flipping because a divider moved is not drift: that is derivation staying honest, and it raises no flag and no tension records.

### The drift flag

Drift is tracked per film: one open flag at most, aggregating every in-tension judgment that implicates the film.
The judgments are the flag's evidence; a flag whose evidence all resolves on its own (for example, the opponent film moves and the judgments become consistent) closes itself, since it has nothing left to stand on.

### Escalation

Quiet phase first: while evidence is thin, the app may slip one targeted drift-check question into a normal comparison moment to confirm or clear the suspicion before bothering the owner.
Loud phase once contradictions exceed what noise explains: the flag surfaces for resolution, and the film is benched as an opponent in other films' placements, since a doubted position is a bent ruler.
Escalation never goes further: no auto-move, no blocked actions.
Surfacing cadence and placement are fixed in [surfacing.md](surfacing.md).

### Resolution

Opening a flag offers three choices: re-place the film ("my opinion changed"), keep the position ("those judgments were noise"), or not now.
Manual dragging to a slot is deliberately not offered; every move goes through comparisons.

Keeping the position gets one light follow-up per implicated opponent: was the contradicting answer noise (the default; the judgment is superseded), or is the opponent the misplaced one?
The latter re-points the tension at the opponent, feeding a drift flag on that film instead, resolvable then or later.

### Re-placement

Re-placement is the standard placement flow, head-started: the film's in-tension judgments count as already-answered questions, so the search resumes from what they imply instead of starting from scratch.
If the evidence disagrees with itself, only the consistent core seeds the search, and fresh questions break the disagreement.
After landing, each of the film's logged judgments is re-evaluated against the new position: consistent ones flip to active, contradicted ones to superseded, and the flag closes.

Four doors open a re-placement: a drift flag, a rewatch, an anchor-designation mismatch, and the owner asking for it outright from the film's page.
Asked outright, a trusted film's search is seeded from its current slot with its old answers set aside, exactly as at a rewatch; a provisional film's search instead keeps every judgment it has collected as an opponent, because those are the evidence its placeholder position was waiting for.
Settling ([onboarding-and-import.md](onboarding-and-import.md)) is that provisional door run over many films in a row.

## Rewatches

Offer, never force.
A rewatch timestamps the watch and asks one light question: still feel the same?
Confirming is a confidence signal for the position; changing your mind enters the same re-placement flow, seeded from the film's current slot (no in-tension evidence exists in this path).
An open drift flag surfaces naturally at the rewatch moment rather than at a random one.

## The anchor lifecycle

- **Designate**: any rated film currently in the target band.
  The app may suggest candidates but never self-designates.
- **Designation mismatch**: designating a film not currently in the band triggers a re-placement seeded by the owner's intent as the ballpark guess, and comparisons always win.
  Landing in the band completes the designation; landing anywhere else cancels it; the re-placement result stands either way, because real judgments are never discarded to protect an intent.
- **Replace**: designating a new canonical film auto-retires the old one; the old film stays where it is.
- **Retire**: changes no ratings and no dividers (dividers derive from judgments about ordinary films).
- **Drift on anchors**: flags form on anchor films like any other; the flag never moves the anchor, it routes the owner into re-placement.
  Re-placing an anchor warns upfront: landing outside its band auto-retires it (a canonical 4.0 living among the 3.5s is a contradiction in terms), landing inside keeps the status.
