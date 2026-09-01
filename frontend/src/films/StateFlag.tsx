import type { LifecycleState } from "../api";

const LABELS: Record<LifecycleState, string> = {
  backlog: "In your backlog",
  watched_unrated: "Watched, not rated",
  rated: "Rated",
};

/**
 * The owner's own-film flag, shown inline wherever films are listed.
 *
 * A rated film shows its value once one exists. Nothing rating-shaped is ever shown for
 * an unwatched film, on any surface, not even on demand (ADR 0005).
 */
export function StateFlag({ state, rating }: { state: LifecycleState | null; rating: number | null }) {
  if (state === null) return null;
  const value = state === "rated" && rating !== null ? ` ${rating.toFixed(1)}` : "";
  return <span className={`state-flag state-flag-${state}`}>{LABELS[state] + value}</span>;
}
