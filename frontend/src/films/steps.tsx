import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type CriteriaCard,
  type FilmCard,
  type Landed as LandedStep,
  type ComparisonAnswer,
  type NarrowStep,
  type Picker as PickerStep,
  type Unlock,
} from "../api";
import { AnchorBadge, AnchorNudge, Band } from "./Band";
import { CriteriaQuestion } from "./Criteria";
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

/**
 * The most bands a range may hold. Two or three adjacent bands is what being unsure
 * between neighbours means (rating-system.md); a fourth is a different problem.
 */
const RANGE_MAX = 3;

/**
 * The bands selected after tapping ``band``, kept a contiguous run of at most three.
 *
 * Adjacency is the whole meaning of a range - "I am unsure between these" is a claim
 * about neighbours - so a tap that cannot extend the run starts a new one rather than
 * selecting a gap. Tapping an end takes it back off, which is how a mis-tap is undone
 * without a control of its own.
 */
function toggled(order: number[], selected: number[], band: number): number[] {
  const index = order.indexOf(band);
  const seats = selected.map((one) => order.indexOf(one)).sort((a, b) => a - b);
  const low = seats.at(0);
  const high = seats.at(-1);
  if (low === undefined || high === undefined) return [band];
  if (index === low && index === high) return [];
  if (index === low) return order.slice(low + 1, high + 1);
  if (index === high) return order.slice(low, high);
  if (index === low - 1 && seats.length < RANGE_MAX) return order.slice(low - 1, high + 1);
  if (index === high + 1 && seats.length < RANGE_MAX) return order.slice(low, high + 2);
  return [band];
}

export function Picker({
  picker,
  tmdbId,
  onLanded,
  onRange,
  footer,
}: {
  picker: PickerStep;
  tmdbId: number;
  onLanded: (landed: LandedStep) => void;
  /** The owner is unsure: these two or three bands go to the comparisons. */
  onRange: (bands: number[]) => void;
  footer: ReactNode;
}) {
  const { busy, error, run } = useAsyncAction();
  const [selected, setSelected] = useState<number[] | null>(null);
  const rating = picker.current_band !== null;
  const order = picker.bands.map((row) => row.band);
  const choosing = selected !== null;

  return (
    <>
      <header className="place-header">
        <h1>{choosing ? "Which are you between?" : rating ? "Rate it again" : "How was it?"}</h1>
        <div className="picker-subject">
          <Poster title={picker.film.title} path={picker.film.poster_path} size="w154" />
          <div>
            <h2>{picker.film.title}</h2>
            <p className="muted">{releaseYear(picker.film.year)}</p>
            <Plot overview={picker.film.overview} />
          </div>
        </div>
        {/* On a re-rate the current band is marked and the current rank shown
            (screens-and-flows.md), and it stays shown while a range is being selected:
            where the film sits now is exactly what the owner is reconsidering. */}
        {rating && (
          <p className="muted picker-current">
            Currently <Band band={picker.current_band} />, number {picker.current_rank} of that
            band.
          </p>
        )}
        {choosing && (
          <p className="muted picker-current">
            Two or three next to each other. A few comparisons will settle it.
          </p>
        )}
      </header>

      {/* Ten rows, each carrying the films the owner is certain of in that band. The
          pools are the whole point: a pick is made against the owner's own references,
          not against a remembered absolute scale. */}
      <ol className="picker" aria-label={choosing ? "Choose a range" : "Pick a rating"}>
        {picker.bands.map((row) => (
          <li key={row.band}>
            <button
              type="button"
              className="picker-band"
              aria-current={row.band === picker.current_band ? "true" : undefined}
              aria-pressed={choosing ? selected.includes(row.band) : undefined}
              disabled={busy}
              onClick={() =>
                choosing
                  ? setSelected(toggled(order, selected, row.band))
                  : void run(async () => onLanded(await api.pickBand(tmdbId, row.band)))
              }
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

      {/* The one-tap pick stays the fastest path on the screen, so the range lives behind
          a line under the rows rather than a mode switch over them: an owner who knows
          the answer never meets it, and an owner who does not is told it is there. */}
      {choosing ? (
        <div className="actions place-answers">
          <button
            type="button"
            className="button"
            disabled={selected.length < 2}
            onClick={() => onRange(selected)}
          >
            Narrow it down
          </button>
          <button type="button" className="button secondary" onClick={() => setSelected(null)}>
            Never mind
          </button>
        </div>
      ) : (
        <p className="muted place-leave">
          <button type="button" className="link-button" onClick={() => setSelected([])}>
            Torn between two?
          </button>{" "}
          Pick a range and answer a few comparisons instead.
        </p>
      )}
      {!choosing && footer}
    </>
  );
}

/**
 * Narrowing a range: the comparisons, the boundary question, and the last-resort pick.
 *
 * The answers live here rather than on the server, which is what keeps the picker
 * stateless: every call hands back the answers given so far and gets the next question
 * from them. Nothing is left behind by walking away - the answers are already in the log
 * as judgments the owner made, and the next attempt starts fresh.
 *
 * An answer that settles the range lands the film without a further screen. The owner has
 * finished answering at that point, and a "confirm your 4.0" step would be exactly the
 * check after every rating this design removed.
 */
export function Narrowing({
  film,
  tmdbId,
  bands,
  onLanded,
  footer,
}: {
  film: FilmCard;
  tmdbId: number;
  bands: number[];
  onLanded: (landed: LandedStep) => void;
  footer: ReactNode;
}) {
  const [step, setStep] = useState<NarrowStep | null>(null);
  const [answered, setAnswered] = useState<ComparisonAnswer[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const { busy, error, run } = useAsyncAction();

  const open = useCallback(async () => {
    try {
      setStep(await api.narrow(tmdbId, bands, []));
    } catch (caught) {
      setFailed(messageOf(caught));
    }
    // The range is fixed for the life of this component - the picker mounts a new one to
    // change it - so this opens the narrowing once and never re-opens it under the owner.
  }, [tmdbId, bands]);

  useEffect(() => {
    void open();
  }, [open]);

  const land = async (band: number, closer?: number) =>
    onLanded(await api.pickBand(tmdbId, band, { bands, answered, closer }));

  const say = (verdict: ComparisonAnswer) =>
    void run(async () => {
      const next = await api.narrow(tmdbId, bands, answered, verdict);
      const transcript = [...answered, verdict];
      setAnswered(transcript);
      if (next.band !== null) {
        onLanded(await api.pickBand(tmdbId, next.band, { bands, answered: transcript }));
        return;
      }
      setStep(next);
    });

  const message = failed ?? error;

  return (
    <>
      {message && (
        <p className="error" role="alert">
          {message}
        </p>
      )}
      {step === null && !failed && <p className="muted">Loading…</p>}

      {step?.question && (
        <>
          <header className="place-header">
            <h1>How does it compare?</h1>
            <p className="muted">
              {step.question.anchor
                ? "One of the films you are sure about."
                : "The film of that band closest to the line."}
            </p>
          </header>
          <div className="compare">
            <Side film={film} caption="The one you just watched" />
            <Side film={step.question.film} caption={<Band band={step.question.band} />} />
          </div>
          <div className="actions place-answers">
            <button type="button" className="button" disabled={busy} onClick={() => say("better")}>
              {film.title} is better
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={() => say("same")}
            >
              About the same
            </button>
            <button type="button" className="button" disabled={busy} onClick={() => say("worse")}>
              {step.question.film.title} is better
            </button>
          </div>
          <p className="muted place-leave">
            <button type="button" className="link-button" disabled={busy} onClick={() => say("skip")}>
              Skip this one
            </button>{" "}
            - you will be asked about another film.
          </p>
        </>
      )}

      {step?.boundary && (
        <>
          <header className="place-header">
            <h1>Which is it closer to?</h1>
            <p className="muted">
              These two sit either side of the line. Whichever it belongs next to is the band it
              lands in.
            </p>
          </header>
          <div className="compare">
            {[
              { film: step.boundary.upper, band: step.boundary.upper_band },
              { film: step.boundary.lower, band: step.boundary.lower_band },
            ].map((side) => (
              <button
                key={side.film.tmdb_id}
                type="button"
                className="compare-choice"
                disabled={busy}
                onClick={() => void run(async () => land(side.band, side.film.tmdb_id))}
              >
                <Side film={side.film} caption={<Band band={side.band} />} />
              </button>
            ))}
          </div>
        </>
      )}

      {step?.choose && (
        <>
          <header className="place-header">
            <h1>Your call</h1>
            <p className="muted">
              Nothing in these bands to compare it against yet, so the choice is yours.
            </p>
          </header>
          <ol className="picker" aria-label="Pick a rating">
            {step.bands.map((band) => (
              <li key={band}>
                <button
                  type="button"
                  className="picker-band"
                  disabled={busy}
                  onClick={() => void run(async () => land(band))}
                >
                  <span className="picker-value">
                    <Band band={band} />
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
      {footer}
    </>
  );
}

/**
 * One film of a comparison: its poster, its name, and what it is standing for.
 *
 * Nothing here is a link. The question is about these two films and the way out of it is
 * an answer or the skip; a title that navigated away would be a trapdoor in the middle
 * of a flow the owner is three taps into.
 */
function Side({ film, caption }: { film: FilmCard; caption: ReactNode }) {
  return (
    <div className="compare-side">
      <Poster title={film.title} path={film.poster_path} size="w342" />
      <span className="film-title">{film.title}</span>
      <span className="muted">{releaseYear(film.year)}</span>
      <span className="compare-caption">{caption}</span>
    </div>
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
      {landed.criteria && <CriteriaRun first={landed.criteria} />}

      <div className="actions place-answers">{primary}</div>
      {footer}
    </>
  );
}

/**
 * The run: the optional questions sitting under the landing they came with.
 *
 * Everything about it is built to cost nothing. It sits beside the primary action rather
 * than over it, so the landing is already complete without it; it never blocks, never
 * navigates, and dismissing it is a real, visible choice rather than a thing to hunt
 * for. Each answer slides the next card in; dismissing, leaving, or running out of
 * questions ends the run, and walking away without touching it is recorded exactly as
 * dismissing it.
 */
function CriteriaRun({ first }: { first: CriteriaCard }) {
  const [gone, setGone] = useState(false);
  if (gone) return null;

  return (
    <CriteriaQuestion
      first={first}
      tag="One more, if you like"
      dismissLabel="No thanks"
      dismissal="ends"
      onDone={() => setGone(true)}
    />
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
