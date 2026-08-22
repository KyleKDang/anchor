# Evaluation reads behavior it never learns from

The v1 design deliberately starves the taste profile of behavioral signals: ranked-tier actions carry no taste meaning ([Ranked tier maintenance policy (#8)](https://github.com/KyleKDang/anchor/issues/8)), discovery accepts feed nothing, and dismissals feed only prose-pattern evidence (ADR 0006).
That raised the question of whether recommender quality could be judged at all without opening a richer feedback channel.
Decided during [Recommender quality evaluation (#19)](https://github.com/KyleKDang/anchor/issues/19): it can, because the feeding constraints bind what may teach the profile, not what may be counted.

The rules:

- **Evaluation may read every recorded event** - vetoes, not-nows, rotations, accepts, dismissals, seen-its, watches, placements - **but nothing evaluation computes ever flows back into the taste profile, the ordering, or any engine decision.**
  Measurement and learning are separate consumers of the same records.
- **The ground truth is the landing**: where an engine pick sits in the ordering once the owner watches and places it.
  A placement is a real taste judgment; every behavioral event is at best a mood proxy.
  Landings are judged only against the same-window landings of the owner's hand-picked watches, never against fixed targets.
- **Provenance is stamped at watch time, never reconstructed.**
  Tier membership churns at session boundaries and no tier history is kept (ADR 0010 rejects event-sourcing), so each watch event records where the film stood when logged - up-next, pool, pinned, or plain backlog - and how it entered the backlog (discovery accept, hand-added, import-seeded).
  A pinned film counts as the owner's pick, never the engine's.
- **Evaluation is operator-facing only.**
  The worker appends one per-account metrics row at each weight-vector retrain (held-out pairwise accuracy on explicit comparisons, plus the evidence counts that contextualize it); everything behavioral is derived by documented SQL queries versioned in the repo.
  No admin UI, no owner-facing quality surface, no targets, no alerting; the demo account is excluded from every aggregate.

The reason is honesty in both directions.
Letting evaluation read behavior costs nothing the design cares about - the profile stays exactly as starved as #8 and #9 decided - while refusing to read it would have forced either flying blind or adding feedback UI the product does not want.
And because placements happen anyway, the strongest possible signal was already in the system; what was missing was attribution, which one small append-only stamp provides.

Rejected: an owner-facing quality surface (dishonest at this data volume and a step toward engine narration, against ADR 0011), thresholds and alerts (with a handful of watches a month every threshold is noise-tripped, and the app's posture is surface evidence, never auto-judge), and arguing for a v2 feedback channel (evaluation never needed richer feedback, only attribution; any future proposal to let behavior teach the profile is a taste-model change that reopens #8, #9, and ADR 0006, not an evaluation need).

## Consequences

- The watch-event stamp is capture-or-lose-forever: it must ship with the first version that logs watches, because past provenance can never be rebuilt.
- The slow signal judges the engine over months, not weeks; the held-out accuracy metric exists precisely to catch a broken scorer immediately while the landings accumulate.
- The LLM discovery layer gets no offline metric in v1; it is judged by landings of accepted films plus dismissal and ignore rates.
- If the slow signal proves too sparse to read, the remedy is patience, never a new channel.
