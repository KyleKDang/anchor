import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router";

import {
  BANDS,
  api,
  type BandQuestion,
  type CriteriaCard,
  type CriteriaVerdict,
  type NeighbourSlot,
  type PlacementLanded,
  type PlacementQuestion,
  type PlacementStep,
  type Verdict,
} from "../api";
import { AnchorBadge, AnchorNudge, Band } from "./Band";
import { Plot } from "./Plot";
import { Poster } from "./Poster";
import { filmPath, releaseYear } from "./tmdb";
import { useAsyncAction } from "./useAsyncAction";

/**
 * The steps of the placement flow, as components two screens drive.
 *
 * Placing one film and settling a library are the same flow with different chrome around
 * it: the same comparison, the same band question, the same landing with the same bonus
 * card. What differs is the way out - one screen offers to finish later, the other to
 * leave the sitting - and, on the landing, what the primary button does next. Both are
 * passed in, so neither screen can drift from the other on the part that must not differ.
 *
 * Every step takes a ``footer``: the line under it that says how to leave. It is a
 * parameter rather than a default because leaving means something different in each - and
 * because a step with no way out of it is the one shape this flow must never render.
 */

export function Comparison({
  step,
  tmdbId,
  anchored,
  onAnswered,
  onGuess,
  footer,
  onJudged,
}: {
  step: PlacementQuestion;
  tmdbId: number;
  anchored: boolean;
  onAnswered: (step: PlacementStep) => void;
  onGuess: (band: number) => void;
  footer: ReactNode;
  /** One judgment was just given. A skip is not one: it records nothing to count. */
  onJudged?: () => void;
}) {
  const { busy, error, run } = useAsyncAction();

  // The pair is echoed back exactly as it was shown, rather than named as "the
  // opponent": most questions here are about the film being placed and some are not,
  // and this screen deliberately cannot tell which kind it just rendered.
  async function answer(verdict: Verdict) {
    await run(async () => {
      onAnswered(await api.answerPlacement(tmdbId, step.a.tmdb_id, step.b.tmdb_id, verdict));
      if (verdict !== "skip") onJudged?.();
    });
  }

  return (
    <>
      <header className="place-header">
        <h1>Which did you like more?</h1>
        {/* No film is named here on purpose. Most of these questions are about the
            film being placed and a few are quiet drift checks about two others, and a
            subtitle saying which would be the one thing that gives them away. */}
        <p className="muted">Judgment {step.answered + 1}</p>
      </header>

      <div className="place-pair">
        {[step.a, step.b].map((film, side) => (
          <section key={film.tmdb_id} className="place-card">
            <Poster title={film.title} path={film.poster_path} size="w342" />
            <h2>{film.title}</h2>
            <p className="muted">{releaseYear(film.year)}</p>
            <Plot overview={film.overview} />
            {/* "This one" rather than the title, which is already the heading right
                above it; the accessible name still says which film it picks. */}
            <button
              type="button"
              className="button"
              aria-label={`${film.title} is better`}
              disabled={busy}
              onClick={() => void answer(side === 0 ? "a" : "b")}
            >
              This one
            </button>
          </section>
        ))}
      </div>

      <div className="actions place-answers">
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => void answer("tied")}
        >
          They're tied
        </button>
        {/* Skip exists so a barely-remembered film never forces a junk judgment. */}
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => void answer("skip")}
        >
          Skip this pair
        </button>
        {/* Offered only once the stars cannot change: stopping before that would leave
            the film with no rating and no way to get one but starting over. */}
        {step.band_locked && (
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() => void run(async () => onAnswered(await api.bailOut(tmdbId)))}
          >
            Good enough, stop here
          </button>
        )}
      </div>

      {step.answered === 0 && anchored && <Ballpark onGuess={onGuess} />}

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
 * The optional ballpark guess: a hunch that opens the search near the right anchor.
 *
 * It is a search seed and nothing else. It never becomes a judgment, never pins a
 * divider, and never sets the rating - the comparisons always win - so it is safe to
 * be wrong about, which is why it can be offered so casually.
 */
function Ballpark({ onGuess }: { onGuess: (band: number) => void }) {
  return (
    <div className="ballpark">
      <span className="muted">Roughly a…</span>
      {BANDS.map((band) => (
        <button key={band} type="button" className="link-button" onClick={() => onGuess(band)}>
          {band.toFixed(1)}
        </button>
      ))}
      <span className="muted">(optional - your answers decide, not the guess)</span>
    </div>
  );
}

/**
 * The band question: the landing sits exactly on a divider, so the owner picks a side.
 *
 * A sliver question compares the film against the two bands' canonical exemplars, which
 * is the question the design actually wants asked. Where a band has nothing to stand for
 * it, the same step degrades to a plain pick between the bands themselves.
 */
export function BandStep({
  step,
  tmdbId,
  onAnswered,
  footer,
  onJudged,
}: {
  step: BandQuestion;
  tmdbId: number;
  onAnswered: (step: PlacementStep) => void;
  footer: ReactNode;
  /** The band pick is a judgment too, and a sitting's tally counts it as one. */
  onJudged?: () => void;
}) {
  const { busy, error, run } = useAsyncAction();

  return (
    <>
      <header className="place-header">
        <h1>{step.sliver ? "Closer in quality to which one?" : "Which band does it belong in?"}</h1>
        <p className="muted">
          It landed right on the line between{" "}
          {step.options.map((option) => option.band.toFixed(1)).join(" and ")}.
        </p>
      </header>

      {/* The film being judged, on screen: the question is about it, and the answer is
          a comparison against the exemplars below. */}
      <div className="band-subject">
        <Poster title={step.film.title} path={step.film.poster_path} size="w154" />
        <span>
          <strong>{step.film.title}</strong>{" "}
          <span className="muted">{releaseYear(step.film.year)}</span>
        </span>
      </div>

      <div className="band-options">
        {step.options.map((option) => (
          <section key={option.band} className="band-option">
            {option.exemplar ? (
              <>
                <Poster
                  title={option.exemplar.title}
                  path={option.exemplar.poster_path}
                  size="w342"
                />
                <h2>{option.exemplar.title}</h2>
                <p className="muted">your {option.band.toFixed(1)}</p>
              </>
            ) : (
              <>
                <p className="band-option-empty">
                  <Band band={option.band} />
                </p>
                <h2>{option.band.toFixed(1)}</h2>
                <p className="muted">nothing here yet</p>
              </>
            )}
            <button
              type="button"
              className="button"
              aria-label={`${step.film.title} is a ${option.band.toFixed(1)}`}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  onAnswered(
                    await api.answerBand(tmdbId, option.band, option.exemplar?.tmdb_id ?? null),
                  );
                  onJudged?.();
                })
              }
            >
              {option.band.toFixed(1)}
            </button>
          </section>
        ))}
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {footer}
    </>
  );
}

export function Landed({
  landed,
  tmdbId,
  onExtended,
  primary,
  footer,
}: {
  landed: PlacementLanded;
  tmdbId: number;
  onExtended: (step: PlacementStep) => void;
  /** What the owner does next: leave with it, or take the next film of a sitting. */
  primary: ReactNode;
  footer: ReactNode;
}) {
  const { busy, error, run } = useAsyncAction();
  const { above, tied_with: tied, below } = landed.neighbours;
  const grouped = [above, below].some((slot) => slot !== null && slot.films.length > 1);

  return (
    <>
      <header className="place-header">
        <h1>{landed.film.title} landed</h1>
        <p className="place-landed-band">
          <Band band={landed.rating} />
          {landed.band_anchor && <AnchorBadge band={landed.rating} />}
        </p>
        <p className="muted">
          Number {landed.position} of {landed.total} in your ordering.
          {landed.provisional && " Still settling - later comparisons will firm it up."}
        </p>
        {landed.designated && (
          <p className="notice">
            It landed in the band, so it is now your canonical {landed.rating?.toFixed(1)}.
          </p>
        )}
      </header>

      {/* Exactly two immediate neighbours, because a neighbour is a *slot*: however many
          films are level with each other above this one, they share a position and get
          the one row that position is (#83). */}
      {/* A row that names two films carries two posters, which would push its title clear
          of the others'. Where any row does, every row widens its poster cell to match and
          the titles line up again; a list with no tie group in it is untouched. */}
      <ol
        className="neighbours"
        aria-label="Immediate neighbours"
        data-grouped={grouped ? "true" : undefined}
      >
        {above && <Neighbour slot={above} rank={landed.position - 1} />}
        <li className="self" aria-current="true" data-tie={tied ? "true" : undefined}>
          <Rank rank={landed.position} tie={tied !== null} />
          <div className="neighbour-posters">
            <Poster title={landed.film.title} path={landed.film.poster_path} size="w92" />
          </div>
          <div className="neighbour-body">
            <span className="film-title">{landed.film.title}</span>
            {/* Level with is not above or below, so it gets its own line rather than
                being spliced into the title. The count is the information the owner came
                for - it is the plainest statement that this position is a placeholder -
                and it is the enumeration that made the old line a wall of titles. */}
            {tied && (
              <span className="neighbour-tie muted">
                Tied with <SlotFilms slot={tied} />
              </span>
            )}
          </div>
        </li>
        {below && <Neighbour slot={below} rank={landed.position + 1} />}
      </ol>

      {landed.anchor_nudge && <AnchorNudge film={landed.film} />}

      {/* The one line the ranked-tier unlock gets, on the screen of the act that earned
          it. It appears once ever and is never repeated: the other half of the moment is
          the nav's one-time dot, and there is no third mention anywhere. */}
      {landed.unlocked && (
        <p className="nudge">
          That was enough to go on. Your <Link to="/watchlist">watchlist</Link> is ranked from
          here: Anchor puts what you are most likely to love next at the top.
        </p>
      )}

      {/* Below the unlock, on the rare landing that carries both: one is news about what
          the owner has just earned, the other is a favour being asked of them. */}
      {landed.criteria && <CriteriaBonus card={landed.criteria} />}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="actions place-answers">
        {primary}
        {/* The doubt alone moves nothing: only the answers this opens can. */}
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => void run(async () => onExtended(await api.keepComparing(tmdbId)))}
        >
          Doesn't look right - keep comparing
        </button>
      </div>
      {footer}
    </>
  );
}

/**
 * The optional bonus question, sitting under the landing it came with.
 *
 * Everything about it is built to cost nothing. It is below the Done button rather than
 * over it, so the owner has already finished before they meet it; it never blocks, never
 * navigates, and dismissing it is a real, visible choice rather than a thing to hunt for.
 * Walking away without touching it is recorded exactly as dismissing it, so the card
 * simply disappears on an answer and says nothing congratulatory afterwards.
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

/**
 * One neighbouring slot: one row, whatever the slot holds.
 *
 * A slot of one reads exactly as a single film always did. A slot of many is a tie
 * group, and it is one row because it is one position - the same fact the Rated wall
 * carries with its joint-rank stamp, which this reuses. The wall keeps a cell per film
 * because it is a grid that can afford eighty of them; this list is four rows on a
 * screen the owner is leaving, so the members past the first couple are a count.
 */
function Neighbour({ slot, rank }: { slot: NeighbourSlot; rank: number }) {
  return (
    <li data-tie={slot.total > 1 ? "true" : undefined}>
      <Rank rank={rank} tie={slot.total > 1} />
      <SlotPosters slot={slot} />
      <div className="neighbour-body">
        <span className="film-title">
          <SlotFilms slot={slot} />
        </span>
      </div>
    </li>
  );
}

/** A rank, marked joint where the position is shared. The wall's mark, its wording too. */
function Rank({ rank, tie }: { rank: number; tie: boolean }) {
  return (
    <span className="ordering-rank">
      {tie && (
        <>
          <span className="visually-hidden">Joint </span>
          <span aria-hidden="true">=</span>
        </>
      )}
      {rank}
    </span>
  );
}

/** A poster per film the row names, so a named film is never left without a face. */
function SlotPosters({ slot }: { slot: NeighbourSlot }) {
  return (
    <div className="neighbour-posters">
      {slot.films.map((film) => (
        <Poster key={film.tmdb_id} title={film.title} path={film.poster_path} size="w92" />
      ))}
    </div>
  );
}

/**
 * The films a slot names, and the rest of it as a count: "Blue Velvet, Fargo and 11 others".
 *
 * The names and the count join like any English list, so the last connector is "and"
 * whether the tail is a title or a number, and a slot of one is just its title.
 */
function SlotFilms({ slot }: { slot: NeighbourSlot }) {
  const others = slot.total - slot.films.length;
  const parts: ReactNode[] = slot.films.map((film) => (
    <Link key={film.tmdb_id} to={filmPath(film.tmdb_id)}>
      {film.title}
    </Link>
  ));
  if (others > 0) parts.push(`${others} ${others === 1 ? "other" : "others"}`);
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {index > 0 && (index === parts.length - 1 ? " and " : ", ")}
          {part}
        </Fragment>
      ))}
    </>
  );
}
