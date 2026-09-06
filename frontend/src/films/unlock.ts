import type { Threshold } from "../api";

/**
 * Which bar a pre-gate screen should talk about, and how far off it is.
 *
 * Both readiness unlocks have a pre-gate screen that names one shortfall - the Watchlist
 * against *ready*, Discovery against *forming* - and the rule for picking which one is
 * the same on both: the film count first whenever it is short, then whichever bar is
 * furthest behind. On a young account every other bar is a ratio over the film count and
 * reads as zero, so "answer more comparisons per film" is true there and no help at all.
 *
 * The rule is shared and the wording is not. Two screens disagreeing about which bar
 * matters would be a bug; two screens phrasing the same shortfall in their own words is
 * the point, because what a Watchlist owner is waiting for is not what a Discovery owner
 * is waiting for.
 */
export function worstBar(thresholds: Threshold[]): Threshold | undefined {
  const films = thresholds.find((one) => one.dimension === "rated_films");
  if (films !== undefined && cleared(films) < 1) return films;
  return [...thresholds].sort((a, b) => cleared(a) - cleared(b))[0];
}

/** How much of one bar is behind the account, capped at all of it. */
export function cleared(threshold: Threshold): number {
  return threshold.need === 0 ? 1 : Math.min(1, threshold.have / threshold.need);
}

/** How many more of whatever the bar counts, always a whole one. */
export function shortfall(threshold: Threshold): number {
  return Math.ceil(threshold.need - threshold.have);
}

/** "s" unless there is exactly one of them. */
export function plural(count: number): string {
  return count === 1 ? "" : "s";
}
