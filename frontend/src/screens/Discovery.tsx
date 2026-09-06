import { useEffect } from "react";

import { api } from "../api";

/**
 * Discovery, which is a placeholder until its own ticket builds the feed.
 *
 * What it does carry already is the arrival: the readiness unlock at *forming* lights a
 * one-time dot on this destination, and a dot has to clear when the owner comes to look
 * at what it was pointing at (surfacing.md). The Watchlist clears its own dot as a side
 * effect of reading the tier; Discovery has no such read yet, so it says so outright.
 */
export function Discovery() {
  useEffect(() => {
    // A dot is the quietest thing on the screen; failing to clear one is not worth a
    // banner, and the next visit asks again.
    void api.seenDiscovery().catch(() => undefined);
  }, []);

  return (
    <>
      <h1>Discovery</h1>
      <div className="empty">
        <p className="muted">Nothing here yet - the feed arrives with its own ticket.</p>
      </div>
    </>
  );
}
