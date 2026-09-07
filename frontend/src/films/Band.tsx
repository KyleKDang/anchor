import { Link } from "react-router";

import { filmPath } from "./tmdb";

/**
 * Showing a band: the half-stars the owner chose, which is the whole of a rating.
 *
 * The band is stored because the owner picked it (ADR 0013), so there is nothing to
 * derive and nothing that can be pending. The null case survives only for the surfaces
 * that show an unrated film, where the honest answer is no rating at all.
 */

/** "★★★★½" for 4.5. The numeric value goes to screen readers, which cannot count stars. */
export function stars(band: number): string {
  return "★".repeat(Math.floor(band)) + (band % 1 === 0 ? "" : "½");
}

export function Band({ band }: { band: number | null }) {
  if (band === null) return <span className="band band-pending">Not rated</span>;
  return (
    <span className="band">
      <span className="band-stars" aria-hidden="true">
        {stars(band)}
      </span>
      <span className="band-value">
        {band.toFixed(1)}
        <span className="visually-hidden"> stars</span>
      </span>
    </span>
  );
}

/** A film the owner has marked as one they are certain of: a definitive 4.0. */
export function AnchorBadge({ band }: { band: number | null }) {
  return (
    <span className="anchor-badge" title={`One of your definitive ${band?.toFixed(1) ?? ""} films`}>
      Anchor
    </span>
  );
}

/**
 * The one ambient line about anchors: shown only while none exists anywhere.
 *
 * Presence-based, and it vanishes the moment the first anchor exists (surfacing.md), so
 * it explains itself once and then never speaks again. Nothing anywhere else asks.
 */
export function AnchorNudge({
  film,
  action,
}: {
  film?: { tmdb_id: number; title: string };
  /** What to do about it, where the toggle is on this very screen. */
  action?: string;
}) {
  return (
    <p className="nudge">
      Marking a film an anchor says you are certain of its rating. Anchors are what the band
      picker shows you when you rate, so you are choosing against your own references.{" "}
      {action ? (
        action
      ) : film ? (
        <Link to={filmPath(film.tmdb_id)}>Start with {film.title}</Link>
      ) : (
        <Link to="/rated">Mark one from any film's page</Link>
      )}
    </p>
  );
}
