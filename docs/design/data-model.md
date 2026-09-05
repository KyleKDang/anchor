# Anchor conceptual data model

Resolved by wayfinder ticket [Data model (#13)](https://github.com/KyleKDang/anchor/issues/13), 2026-08-20, and revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).

This is the conceptual, storage-technology-agnostic model of Anchor's data: entities, relationships, and invariants.
It consolidates mechanics decided by the design docs and introduces no new behavior.
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
| Rating core | Placement, Comparison log entry |
| Watchlist | Tier bookkeeping (membership, pin, veto, cooldown marks) |
| Taste profile | Quality list entry, Profile constraint, Weight vector, Exemplar set, Prose profile version |
| Discovery | Suggestion, Verdict, Feed bookkeeping |

## Shared catalog

### Film

A single movie, identified by its TMDB id.

- Carries the metadata bundle from one bundled TMDB call: title, year, genres, credits, keywords, vote statistics, poster and backdrop paths, plot summary.
- Stamped with a fetch timestamp; a rolling re-sync refreshes still-referenced films older than roughly five months (inside the 6-month terms ceiling, ADR 0003).
- Images are hotlinked from TMDB's CDN; only paths are stored, never image bytes.
- Its vote statistics are what the default order reads, shrunk toward the catalog mean where the count is small.

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
- **Last synced rating**: a nullable value on the rated state - the rating Letterboxd currently holds for the film, initialized by the seed import and overwritten only when the owner marks the film synced.
  The sync list is derived, never stored: the rated films whose current band differs from it, plus those that lack one entirely (never recorded on Letterboxd).
- State-specific attachments: backlog carries the tier bookkeeping, rated carries the placement and the last synced rating, watched-unrated carries the rate-later seat.

### Dismissal

The owner's "not interested" on a discovery suggestion; deliberately orthogonal to the lifecycle state.

- Keyed by (account, film); created only from the feed; permanent until lifted; kept on a reviewable list.
- Can coexist with any lifecycle state: a dismissed film can later be hand-added or watched, at which point the dismissal's suppression is moot (the feed never suggests tracked films anyway) but the record stays.
- Feeds prose-profile regeneration as pattern evidence only, under a magnitude guard (ADR 0006).

### Watch event

A single timestamped watch: an imported diary row, a logged watch, or a rewatch.

- Append-only stream per account: film, timestamp, source, rewatch flag, and for rewatches the optional "still feel the same?" outcome (confirmed, re-rated, or skipped).
- History, not truth: the lifecycle state is authoritative for watched-ness.
  Rated and watched-unrated both mean watched, and a film imported from ratings.csv with no diary rows is legitimately rated with zero watch events.
- **The watch clock** is the count of an account's watch events.
  Every ranked-tier cooldown and staleness measure is denominated in it ("expires at watch #142"), never in calendar time.
  Imported diary events count into the clock; only differences ever matter.

## The rating core

### Placement

The per-film record of where a rated film sits; the ordering is the set of an account's placements ([ADR 0001](../adr/0001-explicit-ordering-not-model-derived.md), [ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).

- **Band**: one of the ten half-star values, the film's rating, chosen by the owner and stored.
- **Rank**: the film's position within its band, 1 the best; dense within a band, shifting on every move.
- **Anchor mark**: set when the owner marks the film, cleared when they retire it or when a move or re-rate carries it into another band.
  A band's anchor pool is its marked placements; there is no separate designation entity and no designation history.
- **Timestamps**: placed-at (the last placement or re-rate, the "recently rated" clock) and moved-at (the last move, absent while the film still holds its default rank).
- **Invariants**: every rated film has exactly one placement; within a band, ranks run 1..n with no gaps; a band is one of the ten values; an anchor is always in the band it was marked in, because the write that takes it out clears the mark.
- **Rating is stored, not derived**: it is the band, and the band is the owner's own choice, so storing it is honest ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md) supersedes [ADR 0002](../adr/0002-anchors-are-centroids-with-derived-dividers.md)).
- A rating in progress needs no entity: the picker holds its range, and the band comparisons it produces are ordinary log entries.

Bands are the fixed vocabulary of ten half-star values, not entities.

### Comparison log entry

The append-only record of every judgment ([ADR 0010](../adr/0010-comparison-log-is-evidence-not-event-source.md): evidence, not an event source).

- Typed: band comparison (the film being rated against an anchor or stand-in; verdict better, worse, about the same, or skip; the range being narrowed), band pick (the film and the band chosen: outright, at the boundary question with its two exemplars named, or as a range's last resort), criteria answer (the pair, the quality, the verdict, including offers ignored or dismissed, which drive the adaptive back-off).
- Each entry carries the films involved, the answer, the moment that produced it (placement, re-rate, criteria run, criteria session), and a timestamp.
- **No status**: an entry the ordering has since been moved past is not marked, superseded, or flagged; a reader compares it with the ordering as it stands, and the ordering wins.
- Never edited, never deleted; the account-realm wipe is the sole exception.
- Criteria entries reference a quality list entry and feed only the taste profile (ADR 0007).
- **Not an event source**: the placements are primary state; replaying the log is not guaranteed to reproduce them and nothing may assume it does (ADR 0010).

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

The canonical films standing for the owner's taste: the anchors, capped to a few per band where a pool is large, plus the ordering's extremes.

Current-only, recomputed mechanically whenever those change.

### Prose profile version

One row per prose-profile regeneration, append-only: version number (monotonic per account), text, generated-at, trigger.

- The latest version is live; every regeneration respects the active profile constraints.
- The version number is the key that caches discovery verdicts, which is why it is a real row and not a bare counter.

### Readiness

Cold, forming, or ready is a derived classification over the rated-film count and the bands spanned (thresholds per [taste-profile.md](taste-profile.md)), never stored authoritatively; at most cached.

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
- The comparison log is append-only, status-free, and never deleted, except by the account-realm wipe; it is evidence, not an event source (ADR 0010).
- Nothing writes the ordering or an anchor mark except the owner's picks, moves, re-rates, and marks; the engine is read-only on all of them (ADR 0001, ADR 0013).
- A rating is a band the owner chose, stored on the placement; the engine never assigns one and no surface derives one.
- Every engine cooldown and staleness measure is denominated in the owner's activity - the watch clock for the tier, the refresh counter for discovery - never in calendar time, so a dormant account never changes behind the owner's back.
- Rating-shaped values for unwatched films exist only inside the scorer and pipeline; none is ever persisted for display or crosses the display boundary (ADR 0005).
