# The comparison log is evidence, not an event source

The comparison log is append-only and records every judgment, which invites reading it as an event store from which the ordering could be replayed.
We decided it is not one: the ordering, dividers, and anchor designations are primary persisted state (per ADR 0001), and the log is the evidence trail behind them - read by the owner, the drift detector, and the advisory math, never replayed to rebuild state.
Chosen because true replayability would force every state-moving act (anchor designations, drift resolutions, imports, divider update rules) into versioned events and would pin replay to the exact placement-algorithm behavior that ran at the time - a heavy rigor bill that buys nothing here, since corruption recovery is already covered by the nightly off-box backups (ADR 0009).

## Consequences

- Replaying the log is not guaranteed to reproduce the current ordering, and no code may assume it does.
- Auditability survives without replayability: every divider move and placement references the judgment that caused it, so any position can be explained even though state cannot be recomputed from the log.
- Backups are the corruption-recovery path for the ordering.
