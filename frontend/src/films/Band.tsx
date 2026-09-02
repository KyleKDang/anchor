import { Link } from "react-router";

import { filmPath } from "./tmdb";

/**
 * Showing a band: half-stars where one has been derived, and the honest gap where none has.
 *
 * A film's rating is which dividers its position sits between, so a film the band
 * structure has not reached yet has no value at all - not a zero, and not a guess. The
 * absence gets a name here ("Rating pending") rather than an empty space, because an
 * empty space reads as a bug and this is the design working as intended.
 */

/** "★★★★½" for 4.5. The numeric value goes to screen readers, which cannot count stars. */
export function stars(band: number): string {
  return "★".repeat(Math.floor(band)) + (band % 1 === 0 ? "" : "½");
}

export function Band({ band }: { band: number | null }) {
  if (band === null) return <span className="band band-pending">Rating pending</span>;
  return (
    <span className="band">
      <span aria-hidden="true">{stars(band)}</span>
      <span className="band-value">
        {band.toFixed(1)}
        <span className="visually-hidden"> stars</span>
      </span>
    </span>
  );
}

/** The canonical exemplar of its band: "this film is what a 4.0 is". */
export function AnchorBadge({ band }: { band: number | null }) {
  return (
    <span className="anchor-badge" title={`The canonical ${band?.toFixed(1) ?? "band"} exemplar`}>
      Anchor
    </span>
  );
}

/** Trusted less than a fully-compared placement; it settles on its own, so it is ambient. */
export function ProvisionalMark() {
  return (
    <span className="provisional-mark" title="Still settling: fewer comparisons than usual">
      settling
    </span>
  );
}

/**
 * The anchor-designation nudge: shown only while no anchor exists anywhere.
 *
 * It lives exactly where the absence of stars is felt and vanishes the moment the first
 * anchor exists, so it explains itself once and then never speaks again.
 */
export function AnchorNudge({ film }: { film?: { tmdb_id: number; title: string } }) {
  return (
    <p className="nudge">
      Your films are in order, but they have no star ratings yet. Pick a film you know
      cold and say what band it is - that is what half-stars are measured against.{" "}
      {film ? (
        <Link to={filmPath(film.tmdb_id)}>Start with {film.title}</Link>
      ) : (
        <Link to="/rated">Choose an anchor</Link>
      )}
    </p>
  );
}
