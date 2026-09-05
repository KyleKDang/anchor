# Anchor

A personal movie taste-engine: ratings anchored in pairwise comparisons instead of a drifting absolute scale, an automatically managed watchlist, and a recommendation engine that learns the owner's taste.

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

**Comparison**:
A single pairwise judgment by the owner: one film ranks above the other, or the two are tied.
Only overall comparisons move the ordering.

**Ordering**:
The sequence of tie-groups over all rated films produced by comparisons.
The durable, drift-proof layer of the rating system: nothing moves except through the owner's judgments or explicit actions.
_Avoid_: shelf, ranking, total order

**Tie-group**:
A set of films occupying a single slot in the ordering: judged equal by the owner (definitive) or seeded equal by the import (provisional).
A provisional tie-group is a placeholder, not a judgment; it dissolves as comparisons pull films out.

**Anchor**:
A film the owner designates as the canonical exemplar of a band (the definitive 4.0).
At most one per band; comparisons cannot move anchors, only the owner can, and an anchor re-placed outside its band is automatically retired.

**Divider**:
The derived boundary between two adjacent bands, sitting between their anchors in the ordering.
Pinned by the owner's band judgments (imported seed ratings count, at lower weight than live answers) and movable as those judgments accumulate; never set directly.

**Band**:
A half-star rating bucket (e.g. 3.5), centered on its anchor and bounded by the dividers on either side.
A band with no anchor still works fully; its dividers persist independently.

**Rating**:
A film's half-star value, derived from which dividers its position sits between.
Never entered directly.
A film placed before any band structure exists shows its position only; its rating materializes as anchors and band judgments accumulate.
_Avoid_: score (ambiguous with recommender scoring)

**Placement**:
The comparison flow that finds a new film's slot in the ordering: seeded by an optional ballpark guess, narrowed against anchors until the band locks, then bisected within the band.

**Ballpark guess**:
An optional half-star estimate the owner gives when logging a film.
Seeds the placement search at the nearest anchor but never sets the rating.

**Sliver question**:
The band-assignment question asked only when a film lands between the highest known film of one band and the lowest known film of the next: closer in quality to which of the two canonical films?

**Plain band pick**:
The last rung of the fallback ladder, where a band the sliver question would name has no exemplar to stand for it: the owner picks the band outright instead of comparing against a film.
Its answer is a band judgment like any other, so it places the film and moves the divider the same way.

**Keep comparing**:
The placement-done screen's option to extend a placement that looks wrong with further comparisons around the landed position, including band-edge anchor questions.
Only the answers can move the film or a divider; the doubt itself never moves anything.

**Provisional placement**:
A film's position trusted less than a fully-compared one: produced by the seed import or by ending a placement early once the band is locked.
Refined by later comparisons; graduates to fully trusted when the advisory math's confidence crosses the same threshold a normal placement needs, or the moment the owner settles it.

**Settling**:
The owner-started flow that runs placements over provisional films one after another until the owner leaves, each film's search head-started by every judgment it has collected as an opponent, so it graduates the moment its own answers pin it.
Entered from the strip atop Rated or from one film's "settling" mark; never offered for anchors, never a target, and leaving mid-film costs nothing.
_Avoid_: ranking session, comparison mode

**Comparison log**:
The append-only record of every judgment: comparisons, skips, band judgments (sliver answers and plain band picks), and criteria answers, each with its context.
Entries are active, in tension (contradicting the ordering), or superseded (settled against by an owner resolution); never deleted (an account reset or deletion is the only exception).

**Criteria question**:
A comparison on a single quality of two films the owner just compared, offered as an optional bonus at the end of a placement - at most one per placement, never blocking, and ignoring or dismissing it is the same as skipping it.
Appears at an adaptive frequency the owner can also set manually or turn off.
Feeds only the taste profile; never moves the ordering.

**Drift**:
The condition where later judgments contradict a film's position in the ordering.
Detected by the app, never auto-corrected; the owner resolves it through the film's drift flag.

**Drift flag**:
The per-film aggregation of the in-tension judgments implicating a film; at most one open flag per film.
Closes when the owner resolves it (re-place, keep, or re-point at the opponent film) or when all its evidence resolves on its own.

**Drift check**:
A targeted comparison the app slips into a normal comparison moment to confirm or clear a suspected drift before surfacing the flag.

**Re-placement**:
The placement flow run again for an already-rated film, entered from drift resolution, a rewatch, an anchor-designation mismatch, or the owner asking for it from the film's page.
Head-started by the film's in-tension judgments where they exist; its outcome always wins over the owner's stated intent.

**Rewatch**:
A repeat watch of a rated film, timestamped internally.
Offers an optional re-placement but never forces one; keeping the current position is a confirming signal.

**Watch event**:
A single timestamped record of the owner watching a film: an imported diary row, a logged watch, or a rewatch.
History, not truth: whether a film counts as watched is carried by its state, so a film imported with a rating but no diary entry is watched despite having no events.

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
Only fully trusted placements appear (provisional films wait for graduation), and a rating that wobbles back to its synced value drops off on its own.
Lives in the Profile's Letterboxd area with an ambient count; never nudges, reminds, or writes anything to Letterboxd itself.

**Watched-unrated film**:
A film marked watched but never rated - imported as watched, or marked seen-it on the discovery feed: outside the ordering, the backlog, and the taste profile.
Its only effects are a discovery-feed dedupe (never recommend a seen film) and a seat in the rate-later queue.

**Rate-later queue**:
The bench of watched-unrated films awaiting an optional placement, kept in view on the rated-films screen.
Fed by the seed import, the "later" choice when logging a watch, seen-it conversions, and abandoned placements; joining it never obliges a rating.
Leaving is equally free: waving a film off the queue never touches its watched status.

**Warmup**:
The skippable guided sequence offered when an account starts: designate anchors, gather first comparison evidence, seed the backlog.
One skeleton with two fills, post-import (import-ranked anchor candidates, a few films settled, watchlist rows) and fresh (search-driven designation, first placements, hand-added films); the app is fully usable the moment any part is skipped.

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

**Exemplar set**:
The canonical films standing for the owner's taste: anchors plus the ordering's extremes.
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
The evidence-based gate on recommendation features: cold (too little signal to train anything), forming (enough for a stable weight vector; discovery lights up; a seed import lands here immediately), ready (enough explicit comparisons and band structure; the ranked tier unlocks).
Measured in evidence, never in time.

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
Where an engine pick sits in the ordering once watched and placed, recorded as a percentile of the ordering.
The ground truth of recommender evaluation: judged only against the same-window landings of the owner's hand-picked watches, never against fixed targets.
Evaluation reads the owner's behavior but never teaches the taste profile with it (ADR 0012).

### Data sources

**No-training provider rule**:
The compliance gate for AI providers: TMDB content and the taste profile may be sent only to APIs whose terms bar training on customer inputs by default.
See ADR 0003 for the full licensing posture, bright lines, and fallback.
