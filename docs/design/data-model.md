# Anchor conceptual data model

Resolved by wayfinder ticket [Data model (#13)](https://github.com/KyleKDang/anchor/issues/13), 2026-08-20.

This is the conceptual, storage-technology-agnostic model of Anchor's data: entities, relationships, and invariants.
It consolidates mechanics decided by earlier design tickets and introduces no new behavior.
Vocabulary follows [CONTEXT.md](../../CONTEXT.md); where a decision has an ADR, it is cited.
"Entity" and "record" mean conceptual units, not tables; the physical schema is implementation work.

## The two realms

Every record lives in exactly one of two realms.

**The shared catalog** holds account-independent facts about films.
It is written by background jobs, read by every account, and never scoped to an owner.

**The account realm** holds everything an owner does and everything derived from it.
Every record in it is owned by exactly one account, and every read is scoped to the logged-in account (single database, owner-scoped rows, per [Architecture, stack, and multi-user model (#12)](https://github.com/KyleKDang/anchor/issues/12)).

Account deletion and the hard-reset re-import wipe the owning account's realm in full; no account operation ever touches the catalog.
That wipe is the one exception to the comparison log's never-deleted rule.

| Realm | Entities |
| --- | --- |
| Shared catalog | Film, Quality vocabulary, Quality tag |
| Account shell | Account, Auth session, Spend ledger entry, Import, Import row |
| Account-film | Account-film, Dismissal, Watch event |
| Rating core | Tie-group slot, Placement, Divider, Anchor designation, Comparison log entry, Drift flag |
| Watchlist | Tier bookkeeping (membership, pin, veto, cooldown marks) |
| Taste profile | Quality list entry, Profile constraint, Weight vector, Exemplar set, Prose profile version |
| Discovery | Suggestion, Verdict, Feed bookkeeping |

## Shared catalog

### Film

A single movie, identified by its TMDB id.

- Carries the metadata bundle from one bundled TMDB call: title, year, genres, credits, keywords, vote statistics, poster and backdrop paths, plot summary.
- Stamped with a fetch timestamp; a rolling re-sync refreshes still-referenced films older than roughly five months (inside the 6-month terms ceiling, ADR 0003).
- Images are hotlinked from TMDB's CDN; only paths are stored, never image bytes.

### Quality vocabulary

The fixed built-in list of qualities: Acting, Screenplay, Direction, Shots, Score, Message (craft); Tension, Pacing, Emotional impact, Ending, Humor, Rewatchability (feel).

- Account-independent and closed: the system never invents entries, and owner customs live in the account realm, not here.
- May physically live in code rather than data; conceptually it is the key space quality tags and built-in quality list entries reference.

### Quality tag

An account-independent marker that a film is known for a vocabulary quality.

- Keyed by (film, vocabulary quality); LLM-precomputed on the cheap tier, once per film ever, cached and shared across accounts.
- Tags draw from the built-in vocabulary only.
  A custom quality is never tagged, so it reaches criteria questions only through the rotation fallback.

## Account shell

### Account

A registered user of Anchor.

- Credentials: email, argon2 password hash, email-verification state.
- A demo flag marks the shared read-only demo account; all writes are disabled for it.
- **Invariant**: an unverified account is fully inert - no rows beyond the account record itself, no TMDB fetches, no imports.

### Auth session

A server-side login session: token, account reference, expiry (httpOnly cookie on the client).

Not to be confused with the "session boundary" at which the ranked tier and feed visibly change; that boundary is a moment in the owner's usage, not a record, and nothing in the model stores it.

### Spend ledger entry

One row per LLM call, append-only: scope (account or shared), operation, model, token counts, computed cost, timestamp.

- The LLM seam checks month-to-date sums against the per-account and global caps (config values) before dispatch; hitting a cap degrades to cached results.

### Import and import row

The one-time Letterboxd seed import.

- **Invariant**: at most one import is in effect per account; importing again is a hard reset that wipes the account realm first and rebuilds from the new export alone.
  There is never a merge.
- The import record: source export identity, timestamp, status.
- One import row per CSV line that matters: raw name, year, URI, kind (rating, watchlist, watched, diary, profile-favorite), and match state - auto-matched, review-pending, bound, unmatched-open, or dismissed.
- An unmatched-open row affects nothing until the owner binds a film or dismisses it; the unmatched list persists indefinitely.

## The account-film relationship

### Account-film

One record per (account, film) pair once any interaction exists; untracked films have no record.

- **Exclusive lifecycle state**: backlog, watched-unrated, or rated.
  A film is never in two of these at once - the exclusions are impossible by construction, not invariants to police.
- Transitions: adding puts an untracked film in backlog; completing a placement makes a film rated (leaving backlog if it was there); "rate later" when logging a watch, a seen-it conversion, or an abandoned placement makes it watched-unrated.
  Rated is left only through the account-realm wipe.
- **Rate-later seat**: a flag valid only in the watched-unrated state.
  Set by the import, the "later" choice, seen-it, and abandoned placements; removable at will, and removing it never touches watched-ness.
- State-specific attachments: backlog carries the tier bookkeeping, rated carries the placement, watched-unrated carries the rate-later seat.

### Dismissal

The owner's "not interested" on a discovery suggestion; deliberately orthogonal to the lifecycle state.

- Keyed by (account, film); created only from the feed; permanent until lifted; kept on a reviewable list.
- Can coexist with any lifecycle state: a dismissed film can later be hand-added or watched, at which point the dismissal's suppression is moot (the feed never suggests tracked films anyway) but the record stays.
- Feeds prose-profile regeneration as pattern evidence only, under a magnitude guard (ADR 0006).

### Watch event

A single timestamped watch: an imported diary row, a logged watch, or a rewatch.

- Append-only stream per account: film, timestamp, source, rewatch flag, and for rewatches the optional "still feel the same?" outcome (confirmed, re-placed, or skipped).
- History, not truth: the lifecycle state is authoritative for watched-ness.
  Rated and watched-unrated both mean watched, and a film imported from ratings.csv with no diary rows is legitimately rated with zero watch events.
- **The watch clock** is the count of an account's watch events.
  Every ranked-tier cooldown and staleness measure is denominated in it ("expires at watch #142"), never in calendar time.
  Imported diary events count into the clock; only differences ever matter.

## The rating core

### Tie-group slot

The ordering is an explicit persisted sequence of slots (ADR 0001); each slot holds one or more rated films judged or seeded equal.

- Each rated film belongs to exactly one slot, via its placement; a slot never sits empty.
- A slot has no status of its own.
  "Provisional tie-group" is shorthand for a slot whose members are all still import-seeded; provisionality proper lives on each film's placement.
- **Invariants**: films sharing a slot definitively are connected by explicit tie judgments; import-seeded slots only ever shrink.
  A Tied answer against a member of a provisional slot pulls that member out into a new definitive two-film slot with the placed film - provisional membership is never inherited, and no film is silently asserted equal to films it was never compared with.

### Placement

The per-film record of where a rated film sits and how much that position is trusted.

- Slot reference, trust (provisional or full), provenance (import-seeded, early-bail, or completed), timestamps.
- A completed placement is fully trusted; import-seeded and early-bail placements start provisional and graduate when the advisory math's confidence crosses the same threshold a normal placement needs.
  The advisory math never moves the slot (ADR 0001).
- **Rating is derived, never stored**: a film's band is whichever pinned dividers its slot sits between.
  While the relevant dividers are unpinned the derivation yields nothing and the film displays position-only.
- An in-progress or abandoned placement needs no entity: its answers are active comparison log entries, and a resume re-derives its search bounds from them.

### Band and divider

Bands are the fixed vocabulary of ten half-star values, not entities.

A divider is the stored boundary marker between two adjacent bands.

- Nine per account, identified by their band pair (for example 3.5/4.0); state is unpinned, or a position between two adjacent slots of the ordering.
- **Positions are stored state, not recomputed on read**: a divider moves only as the direct consequence of a band judgment (sliver answer, seed rating), applied by an update rule that weights live answers above import seeds.
  The triggering judgments live in the comparison log, so every move is auditable; the position itself is authoritative.
- **Invariants**: dividers appear in band order; a band's anchor sits between that band's dividers (a re-placement landing outside auto-retires the anchor); a rating flip caused by a divider move is derivation staying honest, never drift.

### Anchor designation

The current owner-designated exemplar of a band.

- A mapping band to film, at most one per band; the film must be rated.
- Current-only: retirement (owner action or auto-retire on re-placement outside the band) simply clears the mapping and changes no ratings and no dividers, so no designation history is kept.
- Comparisons never move an anchor; only the owner can.

### Comparison log entry

The append-only record of every judgment (ADR 0010: evidence, not an event source).

- Typed: overall comparison (answer A, B, Tied, or Skip - drift checks are overall comparisons in a drift-check context), sliver answer, criteria answer (including unanswered or dismissed offers, which drive the adaptive back-off).
- Each entry carries the films involved, the answer, the moment that produced it (placement of film X, keep-comparing, drift check, warmup, re-placement), and a timestamp.
- Status: active, in-tension (contradicting the ordering), or superseded (settled against by an owner resolution).
  Re-placement re-evaluates the film's entries against the new position: consistent ones flip to active, contradicted ones to superseded.
- Never deleted; the account-realm wipe is the sole exception.
- Criteria entries reference a quality list entry and feed only the taste profile (ADR 0007).
- **Not an event source**: the ordering, dividers, and designations are primary state; replaying the log is not guaranteed to reproduce them and nothing may assume it does (ADR 0010).

### Drift flag

The per-film aggregation of in-tension judgments.

- **Invariant**: at most one open flag per film.
- Evidence: references to the in-tension entries implicating the film; a flag whose evidence all resolves on its own closes itself.
- Stage: quiet (targeted drift checks may be slipped into normal comparison moments) or surfaced (the owner sees it, and the film is benched as a comparison opponent).
- Closes with an outcome: re-placed, kept, re-pointed at the opponent, or self-resolved.

## The watchlist

The backlog is not a separate entity: it is the set of account-films in the backlog state, each with its added-at timestamp, unlimited in size.

### Tier bookkeeping

The ranked tier is persisted visible state hanging off backlog account-films, never derived at read time.

- Membership (at most 30), zone (up-next, at most 5, strictly ordered; or pool), position, entered-at watch-clock value, and a staleness counter of watches survived without being picked.
- **Pin**: a flag with a pin time; pinned films sit in the up-next zone above the engine's picks, ordered by pin time, capped at the zone size, immune to all automatic maintenance.
- **Veto**: a flag barring the film from the tier until lifted, kept on a visible vetoed list; the film stays in the backlog and its score is untouched.
- Cooldown marks (re-entry after rotation or not-now, exit protection after entry) are stored as watch-clock values on the account-film, so they apply whether or not the film currently holds a seat.
- **No staged next tier exists.**
  A session boundary is a moment, not a record: maintenance runs then, computing fresh scores from the current weight vector and applying hysteresis, the swap budget, and cooldowns against this one persisted state.
  Scores themselves are at most a cache, never authoritative.
- The tier exists only at readiness ready; the pre-gate backlog is honestly unranked.
- None of the tier's signals (pin, veto, not-now, rotation, lingering) feed the taste profile in v1.

## The taste profile

### Quality list entry

One entry in the account's canonical quality list.

- Name, origin (a reference to a vocabulary quality for the built-in dozen seeded at account creation, or custom for owner additions via the picker's free text), created-at.
- Built-in and custom entries are treated identically everywhere downstream; the vocabulary reference exists only so shared quality tags can join.

### Profile constraint

A durable owner-stated fact about their taste, stored structurally, never as text edits.

- Kind: a quality-picker selection (referencing a quality list entry) or a prose-profile correction (structured content); active or lifted.
- Respected by every prose regeneration; constraints with a structural footprint (genre, language) are additionally enforced mechanically in the discovery prefilter.

### Weight vector

The numeric artifact: a learned weight per symbolic film feature (ADR 0004), plus its trained-at marker.

Current-only: it retrains from scratch on every ordering change in milliseconds, so history is worthless churn.

### Exemplar set

The canonical films standing for the owner's taste: anchors plus the ordering's extremes.

Current-only, recomputed mechanically whenever those change.

### Prose profile version

One row per prose-profile regeneration, append-only: version number (monotonic per account), text, generated-at, trigger.

- The latest version is live; every regeneration respects the active profile constraints.
- The version number is the key that caches discovery verdicts, which is why it is a real row and not a bare counter.

### Readiness

Cold, forming, or ready is a derived classification over evidence counts and band structure (thresholds per tickets #7 and #17), never stored authoritatively; at most cached.

## Discovery

### Suggestion

One film currently on the feed's shelf.

- Position, verdict reference, refreshes-survived counter, arrived-at refresh value.
- **Invariants**: a film with no verdict never reaches the shelf (the never-pad rule); only untracked, undismissed films are suggested; the shelf runs short rather than pad.
- A suggestion passed over about three refreshes rotates out with a re-entry cooldown denominated in the feed's refresh counter, never calendar time.

### Verdict

The precomputed judgment backing a suggestion.

- Keyed by (account, film, profile version): a coarse fit bucket (strong-fit, plausible, poor-fit - internal only), an exemplar-grounded explanation, listwise rank context, computed-at.
- Append-only across profile versions: a version bump never purges older verdicts.
  The feed uses current-version verdicts when it has them and falls back to the newest available version when degraded (spend caps, the gap right after a bump); genuinely ancient versions are pruned as housekeeping, not semantics.
- Poor-fit verdicts are cached negatives: never shown, never re-sent to the LLM.

### Feed bookkeeping

Per-account counters the feed's economy runs on: last-visited-at, last-restock-at, and the refresh counter that denominates suggestion cooldowns.

Restocks happen only if the owner has visited the feed since the last one; an owner who ignores discovery costs nothing.

## Cross-cutting invariants

- Every account-realm record is owned by exactly one account; account deletion and the hard-reset re-import wipe that realm in full, and the shared catalog is never touched by account operations.
- The comparison log is append-only and never deleted, except by the account-realm wipe; it is evidence, not an event source (ADR 0010).
- Nothing moves the ordering, dividers, or anchors except owner judgments and owner actions; the advisory math is read-only on all of them (ADR 0001).
- Ratings are always derived from position against dividers, never stored or entered.
- Every engine cooldown and staleness measure is denominated in the owner's activity - the watch clock for the tier, the refresh counter for discovery - never in calendar time, so a dormant account never changes behind the owner's back.
- Rating-shaped values for unwatched films exist only inside the scorer and pipeline; none is ever persisted for display or crosses the display boundary (ADR 0005).
