import type { ReactNode } from "react";
import { Link } from "react-router";

import type { Unlock } from "../api";

const UNLOCKED: Record<Unlock, ReactNode> = {
  discovery: (
    <>
      That was enough to go on. <Link to="/discovery">Discovery</Link> is live from here.
    </>
  ),
  watchlist: (
    <>
      Your <Link to="/watchlist">watchlist</Link> is ranked from here: Anchor puts what you are most
      likely to love next at the top.
    </>
  ),
};

/**
 * One line per unlock the owner's last act just earned, and never again.
 *
 * The line rides whichever act crossed the bar - a landing on the picker's done screen,
 * a drop on the wall in the warmup's own step - because that is the screen the owner is
 * looking at when it happens (surfacing.md). The other half of the moment is the nav's
 * one-time dot, and there is no third mention anywhere; an empty list renders nothing.
 *
 * The import's completion screen names its unlocks in its own words rather than through
 * this: an export crosses both bars in one instant, and two of these lines stacked would
 * read as two events where the owner performed one act.
 */
export function UnlockLines({ unlocked }: { unlocked: Unlock[] }) {
  return (
    <>
      {unlocked.map((unlock) => (
        <p key={unlock} className="nudge">
          {UNLOCKED[unlock]}
        </p>
      ))}
    </>
  );
}
