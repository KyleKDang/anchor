# Discovery dismissals are the one queue signal that feeds the taste profile

Anchor's queue actions deliberately carry no taste meaning: pin, veto, not-now, and rotation on the ranked tier were all defined as queue management with zero profile effect ([Ranked tier maintenance policy (#8)](https://github.com/KyleKDang/anchor/issues/8)), and accepting a discovery suggestion follows the same rule, feeding nothing.
Decided during [Discovery feed design (#9)](https://github.com/KyleKDang/anchor/issues/9): dismissing a discovery suggestion is the single exception.
Accumulated dismissals are available to prose-profile regeneration as pattern evidence - "consistently dismisses slow-burn horror suggestions" - under a magnitude guard: a single dismissal means nothing, and only patterns across many surface in the profile.
Dismissals never touch the weight vector, never become profile constraints on their own, and never move the ordering.

The reason is structural blindness.
The ordering only ever contains films the owner chose to watch, so it cannot express "films I would never consider" - and unlike an accepted suggestion, whose taste signal arrives later at full fidelity through watching and placement, a dismissed film produces no watch, no placement, and no later signal at all.
Dismissal patterns are the sole window into that negative space.
The prose profile is the right consumer because it is the artifact built for fuzzy open-world judgment, and because it is owner-visible: if a regeneration writes a dismissal pattern the owner disagrees with, the existing correction flow (profile constraints) overrides it durably.
The separate seen-it action keeps the signal clean: with "already watched" split off, a dismissal reliably means the pitch does not appeal.

## Considered options

- Full symmetry with the ranked tier (dismissals feed nothing): rejected; it would leave the feed permanently unable to learn "stop pitching me these", and no other mechanism ever compensates, because a dismissed film generates no downstream signal.
- Accepts as positive evidence: rejected; a recommender that trains on acceptance of its own suggestions amplifies its own biases in a loop, and the accept signal arrives properly later through placement.
- Per-dismissal hard constraints or weight-vector features: rejected; a single tap is far too noisy to become a durable fact, and the weight vector trains on ordering pairs alone (ADR 0004).
