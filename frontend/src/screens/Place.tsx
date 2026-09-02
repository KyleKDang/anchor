import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import {
  api,
  messageOf,
  type FilmCard,
  type PlacementLanded,
  type PlacementQuestion,
  type PlacementStep,
  type Verdict,
} from "../api";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { filmPath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The placement flow: a full-screen guided flow, one comparison per step.
 *
 * It sits outside the app frame on purpose - no navigation, nothing else on screen - so
 * the only thing to do is answer. All ratings are hidden here, and not just visually:
 * the server never sends one, so the pure which-is-better instinct answers, uncontaminated
 * by the opponent's band.
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

  const begin = useCallback(async () => {
    try {
      setStep(await api.beginPlacement(id));
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [id]);

  useEffect(() => {
    void begin();
  }, [begin]);

  return (
    <div className="place">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {step === null && !error && <p className="muted">Loading…</p>}
      {step?.done === false && <Question step={step} tmdbId={id} onAnswered={setStep} />}
      {step?.done === true && <Landed landed={step} />}
    </div>
  );
}

function Question({
  step,
  tmdbId,
  onAnswered,
}: {
  step: PlacementQuestion;
  tmdbId: number;
  onAnswered: (step: PlacementStep) => void;
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

function Landed({ landed }: { landed: PlacementLanded }) {
  const navigate = useNavigate();
  const { above, tied_with: tied, below } = landed.neighbours;

  return (
    <>
      <header className="place-header">
        <h1>{landed.film.title} landed</h1>
        {/* The position is the whole answer today: a film's band derives from which
            dividers its slot sits between, and none is pinned until #28. */}
        <p className="muted">
          Number {landed.position} of {landed.total} in your ordering.
        </p>
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

      <button type="button" className="button" onClick={() => navigate("/rated")}>
        Done
      </button>
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
