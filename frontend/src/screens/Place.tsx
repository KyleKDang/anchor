import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import {
  BANDS,
  api,
  messageOf,
  type BandQuestion,
  type FilmCard,
  type PlacementLanded,
  type PlacementQuestion,
  type PlacementStep,
  type Verdict,
} from "../api";
import { AnchorBadge, AnchorNudge, Band } from "../films/Band";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { filmPath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The placement flow: a full-screen guided flow, one question per step.
 *
 * It sits outside the app frame on purpose - no navigation, nothing else on screen - so
 * the only thing to do is answer. Ratings are hidden during a comparison, and not just
 * visually: the server never sends one, so the pure which-is-better instinct answers,
 * uncontaminated by the opponent's band. The band question is the one exception, because
 * it is about the bands; hiding them there would leave nothing to answer.
 *
 * There is no undo, deliberately. Leaving is free and safe instead: every answer is
 * already in the append-only log, the film waits in the rate-later queue, and coming
 * back resumes exactly where the owner left off.
 */
export function Place() {
  const { tmdbId } = useParams();
  const id = Number(tmdbId);
  const [step, setStep] = useState<PlacementStep | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [anchored, setAnchored] = useState(false);

  const begin = useCallback(
    async (ballpark?: number) => {
      try {
        setStep(await api.beginPlacement(id, ballpark));
      } catch (caught) {
        setError(messageOf(caught));
      }
    },
    [id],
  );

  useEffect(() => {
    void begin();
    // A ballpark guess seeds the search at the nearest anchor, so with no anchors there
    // is nothing for it to seed and the offer would only be noise.
    api
      .anchors()
      .then((anchors) => setAnchored(!anchors.nudge))
      .catch(() => setAnchored(false));
  }, [begin]);

  return (
    <div className="place">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {step === null && !error && <p className="muted">Loading…</p>}
      {step?.done === false && step.kind === "comparison" && (
        <Comparison
          step={step}
          tmdbId={id}
          anchored={anchored}
          onAnswered={setStep}
          onGuess={(band) => void begin(band)}
        />
      )}
      {step?.done === false && step.kind === "band" && (
        <BandStep step={step} tmdbId={id} onAnswered={setStep} />
      )}
      {step?.done === true && <Landed landed={step} tmdbId={id} onExtended={setStep} />}
    </div>
  );
}

function Comparison({
  step,
  tmdbId,
  anchored,
  onAnswered,
  onGuess,
}: {
  step: PlacementQuestion;
  tmdbId: number;
  anchored: boolean;
  onAnswered: (step: PlacementStep) => void;
  onGuess: (band: number) => void;
}) {
  const { busy, error, run } = useAsyncAction();

  async function answer(verdict: Verdict) {
    await run(async () => onAnswered(await api.answerPlacement(tmdbId, step.b.tmdb_id, verdict)));
  }

  return (
    <>
      <header className="place-header">
        <h1>Which did you like more?</h1>
        <p className="muted">
          Placing {step.a.title} · judgment {step.answered + 1}
        </p>
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

      <div className="place-answers">
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
      <p className="muted place-leave">
        <Link to="/rated">Finish this later</Link> - your answers are kept.
      </p>
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
function BandStep({
  step,
  tmdbId,
  onAnswered,
}: {
  step: BandQuestion;
  tmdbId: number;
  onAnswered: (step: PlacementStep) => void;
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
                void run(async () =>
                  onAnswered(
                    await api.answerBand(tmdbId, option.band, option.exemplar?.tmdb_id ?? null),
                  ),
                )
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
      <p className="muted place-leave">
        <Link to="/rated">Finish this later</Link> - your answers are kept.
      </p>
    </>
  );
}

function Landed({
  landed,
  tmdbId,
  onExtended,
}: {
  landed: PlacementLanded;
  tmdbId: number;
  onExtended: (step: PlacementStep) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();
  const { above, tied_with: tied, below } = landed.neighbours;

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

      <ol className="place-neighbours">
        {above.map((film) => (
          <Neighbour key={film.tmdb_id} film={film} rank={landed.position - 1} />
        ))}
        <li className="place-neighbour place-neighbour-self">
          <span className="place-rank">{landed.position}</span>
          <span>
            {landed.film.title}
            {tied.length > 0 && <> - tied with {tied.map((film) => film.title).join(", ")}</>}
          </span>
        </li>
        {below.map((film) => (
          <Neighbour key={film.tmdb_id} film={film} rank={landed.position + 1} />
        ))}
      </ol>

      {landed.anchor_nudge && <AnchorNudge film={landed.film} />}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="place-answers">
        <button type="button" className="button" onClick={() => navigate("/rated")}>
          Done
        </button>
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
    </>
  );
}

function Neighbour({ film, rank }: { film: FilmCard; rank: number }) {
  return (
    <li className="place-neighbour">
      <span className="place-rank">{rank}</span>
      <Link to={filmPath(film.tmdb_id)}>{film.title}</Link>
    </li>
  );
}
