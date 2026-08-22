# No-nagging surfacing policy

By the time the design's features were resolved, a dozen surfacing moments had accumulated across them: unlock announcements, drift flags, refresh indicators, refinement invitations, import residue.
Decided during [Surfacing and nudge cadence (#18)](https://github.com/KyleKDang/anchor/issues/18): one global posture governs them all, instead of per-feature judgment calls.

The rules:

- **In-app only.** Email is account plumbing (verification, password reset) and never a product channel; no digest, no re-engagement mail, ever.
- **No notification center.** Every moment lives on its one home surface; nothing aggregates, and nothing counts unread.
- **Nothing interrupts.** No self-opening modals, no toasts, no motion the owner did not cause.
  The only proactive shapes are passive elements seen on arrival at a surface, and inline invites at moments the owner triggered.
- **One nav-level marker exists**: a one-time dot, reserved exclusively for the two readiness unlocks (discovery at forming, the ranked tier at ready), clearing on first visit.
  Nothing else ever earns a dot.
- **Ambient counts at an element's own entry point are the loudness ceiling** for everything else.
- **The engine never narrates its background work**: tier refreshes, rotations, rebands, and profile regenerations land unannounced.

The reason is trust.
Engagement conventions - badges, streaks, digests, unread counts - optimize for return visits; Anchor optimizes for the owner's judgment staying unpressured, because comparisons made out of obligation are noise in the one layer that must stay honest.
A corollary the rest of the design already leans on: a dormant account hears nothing at all.

Rejected: a notification inbox (it re-centralizes the nagging and its unread count is a guilt mechanic), product email (exactly the noise the app exists to avoid), and per-feature exceptions (the value of a global rule is that every future feature inherits it by default).

## Consequences

- Every surfacing moment inherited from earlier tickets was placed under this ceiling; the full placement is the resolution of [Surfacing and nudge cadence (#18)](https://github.com/KyleKDang/anchor/issues/18).
- Future features inherit the posture; an exception requires amending this ADR, not a local judgment call.
- The two unlock dots are the loudest the app will ever be.
