import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import {
  api,
  type CriteriaCard,
  type CriteriaVerdict,
  type FilmCard,
  type Landed as LandedStep,
  type Picker as PickerStep,
  type Unlock,
} from "../api";
import { AnchorBadge, AnchorNudge, Band } from "./Band";
import { Plot } from "./Plot";
import { Poster } from "./Poster";
import { filmPath, releaseYear } from "./tmdb";
import { useAsyncAction } from "./useAsyncAction";

/**
 * The band picker and its done screen, as components the rating screen drives.
 *
 * The picker shows the ten bands with the owner's own anchors on each row, and a tap is
 * the whole of rating a film: with the pools on screen the pick has already been made
 * against those references, so nothing follows it but the landing (ADR 0013).
 *
 * Every step takes a ``footer``: the line under it that says how to leave. It is a
 * parameter rather than a default because a step with no way out of it is the one shape
 * this flow must never render.
 */

export function Picker({
  picker,
  tmdbId,
  onLanded,
  footer,
}: {
  picker: PickerStep;
  tmdbId: number;
  onLanded: (landed: LandedStep) => void;
  footer: ReactNode;
}) {
  const { busy, error, run } = useAsyncAction();
  const rating = picker.current_band !== null;

  return (
    <>
      <header className="place-header">
        <h1>{rating ? "Rate it again" : "How was it?"}</h1>
        <div className="picker-subject">
          <Poster title={picker.film.title} path={picker.film.poster_path} size="w154" />
          <div>
            <h2>{picker.film.title}</h2>
            <p className="muted">{releaseYear(picker.film.year)}</p>
            <Plot overview={picker.film.overview} />
          </div>
        </div>
        {rating && (
          <p className="muted picker-current">
            Currently <Band band={picker.current_band} />, number {picker.current_rank} of that
            band.
          </p>
        )}
      </header>

      {/* Ten rows, each carrying the films the owner is certain of in that band. The
          pools are the whole point: a pick is made against the owner's own references,
          not against a remembered absolute scale. */}
      <ol className="picker" aria-label="Pick a rating">
        {picker.bands.map((row) => (
          <li key={row.band}>
            <button
              type="button"
              className="picker-band"
              aria-current={row.band === picker.current_band ? "true" : undefined}
              disabled={busy}
              onClick={() => void run(async () => onLanded(await api.pickBand(tmdbId, row.band)))}
            >
              <span className="picker-value">
                <Band band={row.band} />
              </span>
              <Pool row={row} />
            </button>
          </li>
        ))}
      </ol>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {footer}
    </>
  );
}

/**
 * One band row's anchor pool: a handful of small posters, and a count for the rest.
 *
 * A band with nothing marked shows its label alone rather than an empty frame, because
 * the pool is a reminder and an absent one has nothing to remind anybody of.
 */
function Pool({ row }: { row: PickerStep["bands"][number] }) {
  const others = row.pool_total - row.pool.length;
  if (row.pool.length === 0) return <span className="picker-pool" />;
  return (
    <span className="picker-pool">
      {row.pool.map((film) => (
        <Poster key={film.tmdb_id} title={film.title} path={film.poster_path} size="w92" />
      ))}
      {others > 0 && <span className="muted">+{others}</span>}
    </span>
  );
}

const UNLOCKED: Record<Unlock, ReactNode> = {
  discovery: (
    <>
      That was enough to go on. <Link to="/discovery">Discovery</Link> is live from here.
    </>
  ),
  watchlist: (
    <>
      Your <Link to="/watchlist">watchlist</Link> is ranked from here: Anchor puts what you are
      most likely to love next at the top.
    </>
  ),
};

export function Landed({
  landed,
  primary,
  footer,
}: {
  landed: LandedStep;
  /** What the owner does next: adjust it on the wall, or leave it where it is. */
  primary: ReactNode;
  footer: ReactNode;
}) {
  const { above, below } = landed.neighbours;

  return (
    <>
      <header className="place-header">
        <h1>{landed.film.title} landed</h1>
        <p className="place-landed-band">
          <Band band={landed.band} />
          {landed.anchor && <AnchorBadge band={landed.band} />}
        </p>
        <p className="muted">
          Number {landed.rank} of {landed.band_size} in that band.
        </p>
      </header>

      {/* The rank is a statement about the band, so the films it names are the band's.
          An end of the row simply has no neighbour that way. */}
      <ol className="neighbours" aria-label="Its neighbours in this band">
        {above && <Neighbour film={above} rank={landed.rank - 1} />}
        <li className="self" aria-current="true">
          <span className="ordering-rank">{landed.rank}</span>
          <div className="neighbour-posters">
            <Poster title={landed.film.title} path={landed.film.poster_path} size="w92" />
          </div>
          <div className="neighbour-body">
            <span className="film-title">{landed.film.title}</span>
          </div>
        </li>
        {below && <Neighbour film={below} rank={landed.rank + 1} />}
      </ol>

      {landed.anchor_nudge && <AnchorNudge film={landed.film} />}

      {/* One line per unlock this very landing earned, and never again: the other half of
          the moment is the nav's one-time dot, and there is no third mention anywhere. */}
      {landed.unlocked.map((unlock) => (
        <p key={unlock} className="nudge">
          {UNLOCKED[unlock]}
        </p>
      ))}

      {/* Below the unlock, on the rare landing that carries both: one is news about what
          the owner has just earned, the other is a favour being asked of them. */}
      {landed.criteria && <CriteriaBonus card={landed.criteria} />}

      <div className="actions place-answers">{primary}</div>
      {footer}
    </>
  );
}

/**
 * The optional bonus question, sitting under the landing it came with.
 *
 * Everything about it is built to cost nothing. It is below the primary action rather
 * than over it, so the owner has already finished before they meet it; it never blocks,
 * never navigates, and dismissing it is a real, visible choice rather than a thing to
 * hunt for. Walking away without touching it is recorded exactly as dismissing it, so the
 * card simply disappears on an answer and says nothing congratulatory afterwards.
 *
 * The wording is a fixed template with the quality dropped in. Anchor never invents the
 * question, and this component is the only place the template exists.
 */
function CriteriaBonus({ card }: { card: CriteriaCard }) {
  const { busy, error, run } = useAsyncAction();
  const [gone, setGone] = useState(false);
  if (gone) return null;

  const answer = (verdict: CriteriaVerdict) =>
    void run(async () => {
      await api.answerCriteria(card.id, verdict);
      setGone(true);
    });

  return (
    <section className="criteria" aria-labelledby="criteria-heading">
      <p className="criteria-tag">One more, if you like</p>
      <h2 id="criteria-heading">Which had the better {card.quality.toLowerCase()}?</h2>
      <div className="criteria-films">
        {[
          { film: card.film_a, verdict: "a" as const },
          { film: card.film_b, verdict: "b" as const },
        ].map(({ film, verdict }) => (
          <button
            key={film.tmdb_id}
            type="button"
            className="criteria-film"
            disabled={busy}
            onClick={() => answer(verdict)}
          >
            <span className="criteria-film-body">
              <Poster title={film.title} path={film.poster_path} size="w154" />
              <span className="film-title">{film.title}</span>
              <span className="muted">{releaseYear(film.year)}</span>
            </span>
          </button>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="criteria-actions">
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => answer("tied")}
        >
          Tied
        </button>
        <button type="button" className="link-button" onClick={() => setGone(true)}>
          No thanks
        </button>
      </div>
    </section>
  );
}

/** One neighbouring film: its rank in the band, its poster, and its title. */
function Neighbour({ film, rank }: { film: FilmCard; rank: number }) {
  return (
    <li>
      <span className="ordering-rank">{rank}</span>
      <div className="neighbour-posters">
        <Poster title={film.title} path={film.poster_path} size="w92" />
      </div>
      <div className="neighbour-body">
        <span className="film-title">
          <Link to={filmPath(film.tmdb_id)}>{film.title}</Link>
        </span>
      </div>
    </li>
  );
}
