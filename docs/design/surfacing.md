# Surfacing and nudge cadence

Consolidates wayfinder ticket [Surfacing and nudge cadence (#18)](https://github.com/KyleKDang/anchor/issues/18): every surfacing moment the design creates, placed under one global posture, recorded as [ADR 0011](../adr/0011-no-nagging-surfacing-policy.md); revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).
The posture exists for trust: engagement conventions optimize for return visits, while Anchor optimizes for the owner's judgment staying unpressured, because a pick or a move made out of obligation is noise in the one layer that must stay honest.

## Ground rules

1. **In-app only.**
   Email stays account plumbing (verification, password reset); no product moment ever uses it.
2. **No notification center.**
   Each moment lives on its one home surface; nothing aggregates, nothing counts unread.
3. **Nothing interrupts.**
   No self-opening modals, no toasts, no motion the owner did not cause.
   The only proactive shapes: passive on-arrival elements, and inline invites at moments the owner triggered.
4. **The one-time dot** is the only nav-level marker, reserved exclusively for the two readiness unlocks (Discovery at *forming*, Watchlist at *ready*), clearing on first visit.
   Nothing else ever gets a dot.
5. **Ambient counts** at an element's own entry point are the loudness ceiling for everything else.
6. **The engine never narrates its background work.**

A corollary the rest of the design leans on: a dormant account hears nothing at all.
Future features inherit the posture; an exception requires amending ADR 0011, never a local judgment call.

## Moment placements

- **Readiness unlocks**: the dot, plus one line on the placement-done screen (or warmup step) when that very act crossed the bar; an import that crosses both bars at once earns both dots, and the import's completion screen names what just unlocked.
  Progress is ambient only: a line and subtle bar on the pre-gate Watchlist counted in films rated, the readiness display on Profile, and the pre-gate Discovery screen explaining itself.
- **The wall**: a pull surface through and through.
  No film is ever marked as wanting attention, no move is ever suggested, and edit mode announces nothing but the result of each drop.
- **Anchors**: one ambient line in edit mode while the account has no anchor, saying what marking one does; presence-based, vanishing the moment the first anchor exists.
  Nothing anywhere else asks for one.
- **The criteria run**: an inline invite on the done screen, at a moment the owner triggered, governed by the frequency setting; the loudest a criteria question ever gets.
  The criteria session is pull-only, one control on the film page.
- **Rebands**: a film moved across bands is never announced as an event; visibility is the sync list's job.
- **Tier refresh**: unmarked; the tier is simply its new self (a just-backlogged film entering immediately is its own confirmation).
  **Rotation**: never announced.
- **Prose profile**: an ambient "last updated" line on the profile page, nothing more.
- **Rate-later queue**: the Rated-screen section with its count is the ceiling; no post-placement chasers - "later" never becomes a promise.
- **Fresh suggestions**: in-feed "new since your last visit" markers on cards, positions untouched; nothing at nav level (freshness, not fit, so [ADR 0005](../adr/0005-no-rating-shaped-predictions.md) is untouched).
- **Import residue**: the review queue is offered inline when matching completes; deferred, it and the unmatched list become ambient counts in Profile's Letterboxd area beside the sync list, and are never mentioned anywhere else.
- **Pin / veto / not-now**: the visible effect is the confirmation; each action's inverse is its undo; veto's presentation always reads "not from my queue", never distaste.

## The sync list

Anchor never writes to Letterboxd; the sync list is how the owner keeps one rating set by hand.

- Every rated film carries a **last synced rating**: what Letterboxd holds, as far as Anchor knows - initialized by the seed import, updated only when the owner marks the film synced.
- Films whose current rating differs appear on the list showing old → new, with per-film "synced" and a mark-all; the list is self-cleaning when a rating wobbles back.
- Fresh Anchor ratings never recorded on Letterboxd join as a not-yet-on-Letterboxd section.
- A film moved or re-rated into another band appears at once, so the list is empty right after import and fills only as the owner corrects the wall.
- Home: Profile's Letterboxd area, ambient count, no reminders, no write-back.
