import type { LifecycleState } from "../api";

const LABELS: Record<LifecycleState, string> = {
  backlog: "In your backlog",
  watched_unrated: "Watched, not rated",
  rated: "Rated",
};

/**
 * What the owner already knows about a film, shown inline wherever films are listed.
 *
 * A rated film shows its value once one exists. Nothing rating-shaped is ever shown for
 * an unwatched film, on any surface, not even on demand (ADR 0005).
 */
export function Standing({ state, rating }: { state: LifecycleState | null; rating: number | null }) {
  if (state === null) return null;
  const value = state === "rated" && rating !== null ? ` ${rating.toFixed(1)}` : "";
  return <span className={`standing standing-${state}`}>{LABELS[state] + value}</span>;
}
