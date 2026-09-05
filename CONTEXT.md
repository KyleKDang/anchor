# Anchor

A personal movie taste-engine: ratings anchored to the films the owner is sure of and ordered by hand on a visible wall, instead of a drifting absolute scale; an automatically managed watchlist; and a recommendation engine that learns the owner's taste.

## Language

### Accounts

**Account**:
A registered user of Anchor with fully separate data: ratings, ordering, anchors, watchlist, and taste profile.
Accounts do not interact with each other.

**Owner**:
The account whose data is in question.
Every rating, comparison, ordering, and watchlist belongs to exactly one owner.

**Demo account**:
The shared read-only account offered from the landing page so a visitor can explore a fully lived-in Anchor without registering.
Its content is a curated fixture built from the developer's own judgments, replayed through the real engine and presented without an owner identity.
Nothing in it moves: visitor writes are rejected and the engine's own maintenance skips it, so it changes only when its fixture is rebuilt.

### Rating

**Film**:
A single movie, identified by its TMDB entry.

**Ordering**:
The ten band rows of every rated film, best band first, each row a strict order of the films in it.
The durable layer of the rating system: it changes only through the owner's own picks and moves, never through anything the engine computes.
_Avoid_: shelf, ranking, total order, tie

**Band**:
One of the ten half-star values, and the row of the ordering holding every film the owner rated that value.
A film's band is its rating.

**Rank**:
A film's position within its band, 1 being the band's best.
Set by the owner's moves; until a film is moved it holds the rank the default order gave it.

**Rating**:
A film's band, chosen by the owner on the band picker or by a move on the wall.
Never typed as a number and never assigned by the engine.
_Avoid_: score (ambiguous with recommender scoring)

**Anchor**:
A film the owner has marked as one they are certain of: a definitive 5.0, a definitive 3.5.
Any number per band, and together a band's anchors are its anchor pool: the references the band picker shows for the band, and the opponents its comparisons draw on.
An anchor sits wherever it sits in its band, so the pool of a top band is naturally that band's best and the pool of a middle band its typical members; an anchor bounds a comparison and never acts as a floor or a ceiling.
Moving an anchor to another band retires it, because a reference that moved is no longer certain.

**Stand-in**:
The film that stands for a band in a comparison when the band has no anchor left to ask about: its film nearest the boundary in question.

**Band picker**:
The screen that rates a film: the ten bands, each showing its anchor pool, from which the owner picks one band outright or selects a range.

**Range**:
The two or three adjacent bands an owner unsure of a film selects on the band picker.
Narrowed to one band by comparisons against those bands' anchors, and at the seam by the boundary question.

**Comparison**:
A single pairwise judgment by the owner.
A band comparison sets the film being rated against an anchor or stand-in of a band in its range: better, worse, or about the same.
It bounds which band the film can land in and decides nothing else about the ordering.
A criteria question is the other kind, and bears on the ordering not at all.

**Boundary question**:
The last question a two-band range can need, when anchors have bounded the film to the seam between the bands: the bottom film of the upper band and the top film of the lower, and which the film is closer to.
A band pick with two exemplars; it lands the film beside the film it was judged closer to.

**Placement**:
Rating a film for the first time: the band picker, the comparisons a range needs, and the landing at the film's default rank in its band, from which the wall opens in edit mode so the owner can move it.

**Default order**:
The order films in a band take before the owner moves them: by TMDB average, shrunk toward the catalog mean where votes are few, so an obscure film with a handful of perfect votes does not top a row.
Seeds every imported band and seats every newly rated film.

**Edit mode**:
The Rated wall's editing state: the owner drags films within and across bands, marks anchors, and every drop saves at once.

**Move**:
The owner dragging a film to a new rank or band in edit mode.
The only writer of the ordering besides placement and re-rating.

**Re-rate**:
The band picker run again for a rated film, from a rewatch or from the film's own page.
Landing in the same band keeps the rank; landing in a new band takes the default rank there and retires the film as an anchor if it was one.

**Rewatch**:
A repeat watch of a rated film, timestamped internally.
Offers an optional re-rate but never forces one; keeping the current rating is a confirming signal.

**Watch event**:
A single timestamped record of the owner watching a film: an imported diary row, a logged watch, or a rewatch.
History, not truth: whether a film counts as watched is carried by its state, so a film imported with a rating but no diary entry is watched despite having no events.

**Comparison log**:
The append-only record of every judgment: band comparisons, band picks, and criteria answers, each with its context.
Entries are never edited and never deleted (an account reset or deletion is the only exception).
A judgment the ordering has since been moved past is not marked; it is read against the ordering as it stands, and the ordering wins.

**Criteria question**:
A comparison on a single quality of two films: which had the better screenplay, the better ending.
Offered as a run of cards on the placement-done screen and as an open-ended session from any rated film's page.
Feeds only the taste profile; never moves the ordering.

**Criteria session**:
The open-ended stream of criteria questions about one rated film, entered from its page and left whenever the owner likes.
The comparison idea kept as an optional way to teach Anchor about a film, never as a chore.

**Seed import**:
The one-time import of an owner's Letterboxd CSV export that bootstraps their ordering, backlog, and watch history.
Anchor has no live Letterboxd connection; this is the only data that crosses over.
Importing over existing account data, seeded or organic, is allowed only as a hard reset: behind an explicit warning that enumerates what will be destroyed, it wipes all account data and rebuilds from the new export alone.

**Unmatched row**:
An import row with no bound TMDB film (TV-side entries, deleted films, failed searches).
Affects nothing until the owner binds a film manually or dismisses it; the unmatched list stays open indefinitely.

**Last synced rating**:
The rating Letterboxd currently holds for a film, as far as Anchor knows: initialized by the seed import, updated only when the owner marks the film synced.
Absent for films never recorded on Letterboxd.

**Sync list**:
The pull-only list of films whose current rating differs from their last synced rating, shown old to new so the owner can carry the update to Letterboxd by hand; fresh Anchor ratings never recorded there join as a not-yet-on-Letterboxd section.
A rating that wobbles back to its synced value drops off on its own.
Lives in the Profile's Letterboxd area with an ambient count; never nudges, reminds, or writes anything to Letterboxd itself.

**Watched-unrated film**:
A film marked watched but never rated - imported as watched, or marked seen-it on the discovery feed: outside the ordering, the backlog, and the taste profile.
Its only effects are a discovery-feed dedupe (never recommend a seen film) and a seat in the rate-later queue.

**Rate-later queue**:
The bench of watched-unrated films awaiting an optional placement, kept in view on the rated-films screen.
Fed by the seed import, the "later" choice when logging a watch, seen-it conversions, and abandoned placements; joining it never obliges a rating.
Leaving is equally free: waving a film off the queue never touches its watched status.

**Warmup**:
The skippable guided sequence offered when an account starts: mark anchors, look over the wall, seed the backlog.
One skeleton with two fills, post-import (import-ranked anchor candidates per band, a first look at the wall in edit mode, watchlist rows) and fresh (search-driven anchors rated through the picker, a few first films, hand-added films); the app is fully usable the moment any part is skipped.

### Watchlist

**Backlog**:
The unlimited tier of the watchlist: every unwatched film the owner has added.

**Ranked tier**:
The small ordered tier of the watchlist (capped at 30 films): what to watch next.
Generated and continuously maintained by the recommendation engine; draws only from the backlog; exists only at taste-profile readiness ready.
Split into the up-next zone and the pool; manual overrides (pin, veto, not-now) are exceptions, not the workflow.
Never shows anything rating-shaped for an unwatched film: position is the entire public statement (ADR 0005).

**Up-next zone**:
The strictly ordered top of the ranked tier (five films): a genuine "watch these next" claim.
Pinned films sit here above the engine's picks; its order is protected from casual reshuffling.

**Pool**:
The loosely ordered remainder of the ranked tier.
Its internal order floats freely with the scorer; membership changes are damped so the tier never churns faster than it earns.

**Pin**:
The override that puts or holds a backlog film in the up-next zone, above the engine's picks, immune to all automatic maintenance.
Ordered by pin time, capped at the zone size; a pinned film leaves only by being watched, unpinned, or removed from the backlog.

**Veto**:
The override that bars a backlog film from the ranked tier until the owner lifts it.
The film stays in the backlog with its score untouched; vetoing says "not from my queue", never "I'd dislike it", so it carries no taste-profile effect.

**Not-now**:
The lightweight action that rotates a ranked-tier film out immediately with the standard re-entry cooldown.
Manual staleness: a mood signal, never a taste signal, and never fed to the taste profile.

**Rotation**:
The demotion of a stale ranked-tier pick: one repeatedly passed over, measured in the owner's logged watches, never calendar time.
The film leaves with a re-entry cooldown and an untouched score, so a genuinely strong film returns later; a dormant account never rotates.

**Watch clock**:
The running count of an account's watch events: the unit every ranked-tier cooldown and staleness measure is denominated in, never calendar time.
A dormant account's clock stands still, so nothing rotates or expires while the owner is away.

### Taste profile

**Taste profile**:
What the recommendation engine has learned about the owner's preferences: three artifacts derived from the one ordering - the weight vector, the exemplar set, and the prose profile.
Regenerated on change, never incrementally patched.
Owns the ranked tier of the watchlist and drives the discovery feed.

**Weight vector**:
The numeric artifact of the taste profile: a learned weight per film feature, retrained from scratch on every ordering change.
Scores any film instantly; ranks the backlog and prefilters discovery candidates.
The only scorer that runs at request time.
Reads the ordering the way the owner means it: bands as judgments, within-band order as a range.

**Exemplar set**:
The canonical films standing for the owner's taste: the anchors, a few per band where a pool is large, plus the ordering's extremes.
Recomputed mechanically whenever those change; supplies concrete examples for discovery prompts and explanations.

**Prose profile**:
The owner-readable description of the owner's taste, LLM-maintained and versioned.
Regenerates on accumulated change with a staleness backstop, never per comparison, and every regeneration must respect the owner's profile constraints.
Drives discovery reranking; visible to the owner, never a required step.

**Profile version**:
The marker bumped by each prose-profile regeneration.
Cached recommendation judgments are keyed by film and profile version, so a bump is what schedules re-scoring.

**Quality**:
A named aspect of films the owner's taste can favor: craft (acting, screenplay, shots, score, message) or feel (tension, ending, rewatchability), never right-now mood.
One canonical list per account - the built-in list plus the owner's custom additions, all treated identically - shared by the quality picker and criteria questions; the system never invents entries.

**Quality tag**:
A precomputed, account-independent marker that a film is known for a quality.
Computed once per film, cached and shared across accounts; a criteria question asks about a quality both films are tagged with, falling back to rotation through the list.

**Quality picker**:
The skippable multi-select of favored qualities, drawn from the account's quality list, offered at profile creation and editable later, pre-checked with suggestions inferred from the owner's judgments (criteria answers included).
Selections become profile constraints; free text is an optional escape hatch that adds a custom quality to the list, never required.

**Profile constraint**:
A durable owner-stated fact about their taste: a quality-picker selection or a correction made on the prose profile.
Stored structurally, never as text edits, and respected by every regeneration.

**Taste profile readiness**:
The evidence-based gate on recommendation features: cold (too few rated films to train anything), forming (enough films across enough bands for a stable weight vector; discovery lights up), ready (more films, same spread; the ranked tier unlocks).
A seed import of any real size lands at ready the moment matching completes.
Measured in films and bands, never in time.

### Discovery

**Discovery feed**:
The bounded shelf of suggested films from the wider catalog - films the owner has never added, offered for the backlog.
A flat list in which position is the entire public statement (ADR 0005): no fit labels, and each film's explanation speaks only in exemplars.
Kept separate from the ranked tier: accepting lands a film in the backlog, where it competes on the same terms as any hand-added film, and the feed never writes to the tier.

**Suggestion**:
A film currently on the discovery feed, acted on by accept, dismissal, or seen-it.
One passed over for several refreshes rotates out with a re-entry cooldown, measured in refreshes survived, never calendar time; a dormant account never rotates anything.

**Verdict**:
The precomputed judgment backing a suggestion: a coarse fit bucket and an exemplar-grounded explanation, keyed by film and profile version.
Buckets stay internal - only the explanation is ever shown - and a film with no verdict never reaches the feed, whatever the spend state; the shelf runs short rather than pad.

**Dismissal**:
The owner's "not interested" on a suggestion: permanent-until-lifted suppression from the feed, kept on a reviewable dismissed list, touching no other surface.
Accumulated dismissals feed prose-profile regeneration as pattern evidence only, the one queue signal in Anchor that feeds the taste profile (ADR 0006).

**Seen-it**:
The owner's "already watched this" on a suggestion: converts the film to a watched-unrated film and offers an optional, skippable placement.
Kept separate from dismissal so a dismissal cleanly means the pitch does not appeal.

**Restock**:
The re-pull of discovery candidate pools that tops up the feed's pipeline.
Happens only when the owner has visited the feed since the last one; an owner who ignores discovery costs nothing.

### Evaluation

**Watch source**:
The stamp recorded on a watch event at logging time: where the film stood (up-next, pool, pinned, or plain backlog) and how it entered the backlog (discovery accept, hand-added, or import-seeded).
Captured in the moment because tier history is never kept; a pinned film counts as the owner's pick, never the engine's.

**Landing**:
Where an engine pick sits in the ordering once watched and rated, recorded as a percentile of the ordering.
The ground truth of recommender evaluation: judged only against the same-window landings of the owner's hand-picked watches, never against fixed targets.
Evaluation reads the owner's behavior but never teaches the taste profile with it (ADR 0012).

### Data sources

**No-training provider rule**:
The compliance gate for AI providers: TMDB content and the taste profile may be sent only to APIs whose terms bar training on customer inputs by default.
See ADR 0003 for the full licensing posture, bright lines, and fallback.

**LLM operations seam**:
The one module exposing Anchor's four LLM jobs - candidate reranking, prose regeneration, quality tagging, and picker suggestions - each schema-validated, behind a provider adapter.
Only the worker imports it, which is what makes the precompute-only rule structural: no interactive request path can wait on an LLM call.
The no-training provider rule is enforced here, in code.

**Spend ledger**:
The append-only record of every LLM call: its scope (one account, or shared), operation, model, tokens, and computed cost.
The seam sums it month-to-date before each dispatch against the per-account and platform-wide caps; hitting either skips the work and serves cached results.
Kept through a re-import, unlike the account's film data, so re-importing can never reset a cap.
