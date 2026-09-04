import { useState } from "react";
import { useNavigate } from "react-router";

import {
  api,
  type AnchorPhase,
  type AnchorPrompt,
  type FilmCard,
  type SearchResult,
  type Warmup,
} from "../../api";
import { stars } from "../../films/Band";
import { Poster } from "../../films/Poster";
import { placePath, releaseYear } from "../../films/tmdb";
import { useAsyncAction } from "../../films/useAsyncAction";
import { FilmPicker } from "./FilmPicker";

/**
 * Phase 1: name what each band means, which is the one direct band assignment there is.
 *
 * One prompt at a time rather than ten at once. The five whole stars come in the order
 * they are easiest to answer - best, worst, middle, then the two that only become
 * findable once their neighbours exist - and asking them all together would throw that
 * away and turn a two-minute flow into a form.
 *
 * The app never designates. Everything here offers, ranks, and gets out of the way; the
 * write happens on the owner's own tap and nowhere else (ADR 0002).
 */
export function Designate({
  phase,
  fill,
  onChanged,
}: {
  phase: AnchorPhase;
  fill: Warmup["fill"];
  onChanged: (warmup: Warmup) => void;
}) {
  const [continuing, setContinuing] = useState(false);
  const queue = continuing ? [...phase.prompts, ...phase.continuation] : phase.prompts;
  const current = queue.find((prompt) => prompt.state === "todo") ?? null;
  const done = queue.filter((prompt) => prompt.state !== "todo").length;

  return (
    <>
      <ol className="anchor-track" aria-label="Bands to anchor">
        {queue.map((prompt) => (
          <TrackMark key={prompt.band} prompt={prompt} current={prompt === current} />
        ))}
      </ol>

      {current === null ? (
        <Finished
          phase={phase}
          continuing={continuing}
          onContinue={() => setContinuing(true)}
        />
      ) : (
        <Prompt
          prompt={current}
          fill={fill}
          position={done + 1}
          total={queue.length}
          onChanged={onChanged}
        />
      )}
    </>
  );
}

/** One band's standing in the run, so the owner can see how much is left of it. */
function TrackMark({ prompt, current }: { prompt: AnchorPrompt; current: boolean }) {
  return (
    <li
      className={`anchor-track-mark${current ? " current" : ""}`}
      data-state={prompt.state}
      aria-current={current ? "step" : undefined}
    >
      <span className="band-value">{prompt.band.toFixed(1)}</span>
      <span className="visually-hidden">
        {prompt.state === "done" ? "anchored" : prompt.state === "skipped" ? "skipped" : "to do"}
      </span>
    </li>
  );
}

function Finished({
  phase,
  continuing,
  onContinue,
}: {
  phase: AnchorPhase;
  continuing: boolean;
  onContinue: () => void;
}) {
  const anchored = [...phase.prompts, ...phase.continuation].filter((one) => one.film !== null);
  const more = phase.continuation.some((one) => one.state === "todo");

  return (
    <>
      {anchored.length === 0 ? (
        <p className="muted">
          No anchors yet. Your films will sit in order and show their positions; the half-stars
          arrive when a band has an exemplar to measure against.
        </p>
      ) : (
        <ul className="anchor-set">
          {anchored.map((prompt) => (
            <li key={prompt.band} className="anchor-set-item">
              <span className="band-stars" aria-hidden="true">
                {stars(prompt.band)}
              </span>
              <span className="band-value">{prompt.band.toFixed(1)}</span>
              <span className="film-title">{prompt.film?.title}</span>
            </li>
          ))}
        </ul>
      )}
      {/* Offered, never prompted: "a definitive 3.5" is a harder judgment than "a
          definitive 3", so the half-stars wait behind a door the owner opens. */}
      {!continuing && more && (
        <button type="button" className="button secondary" onClick={onContinue}>
          Set half-star bands too
        </button>
      )}
    </>
  );
}

function Prompt({
  prompt,
  fill,
  position,
  total,
  onChanged,
}: {
  prompt: AnchorPrompt;
  fill: Warmup["fill"];
  position: number;
  total: number;
  onChanged: (warmup: Warmup) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();

  /**
   * Designating a film the owner has never placed: mark it watched, then name the band.
   *
   * Two calls rather than one, because each says a true thing on its own. "This is what
   * a 4.0 is" is a claim only somebody who has seen the film can make, so watched comes
   * first - and if the second call never happens, the film is simply waiting in the
   * rate-later queue, which is where any watched film rests anyway.
   */
  async function designateFresh(film: SearchResult) {
    await run(async () => {
      if (film.state === null || film.state === "backlog") {
        await api.markWatched(film.tmdb_id, "later");
      }
      const outcome = await api.designate(prompt.band, film.tmdb_id);
      if (outcome.outcome === "designated") onChanged(await api.warmup());
      else void navigate(`${placePath(film.tmdb_id)}?back=/warmup`);
    });
  }

  async function designateCandidate(film: FilmCard) {
    await run(async () => {
      const outcome = await api.designate(prompt.band, film.tmdb_id);
      if (outcome.outcome === "designated") onChanged(await api.warmup());
      else void navigate(`${placePath(film.tmdb_id)}?back=/warmup`);
    });
  }

  return (
    <div className="prompt">
      <p className="prompt-step muted">
        Band {position} of {total}
      </p>
      <h3 className="prompt-question">
        <span className="band-stars" aria-hidden="true">
          {stars(prompt.band)}
        </span>{" "}
        Which film is a definitive {prompt.band.toFixed(1)}?
      </h3>
      <p className="muted">
        Pick one you know cold. Everything else in this band gets measured against it, so a
        film you are sure about is worth more than a film you love.
      </p>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {fill === "imported" ? (
        <Candidates prompt={prompt} disabled={busy} onPick={designateCandidate} />
      ) : (
        <FilmPicker
          label={`Find your ${prompt.band.toFixed(1)}`}
          action={`This is my ${prompt.band.toFixed(1)}`}
          browse
          disabled={busy}
          onPick={designateFresh}
        />
      )}

      <p className="prompt-skip">
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() => void run(async () => onChanged(await api.skipWarmup("anchors", prompt.band)))}
        >
          Skip this band
        </button>
      </p>
    </div>
  );
}

/**
 * The films the account already holds in this band, best-remembered first.
 *
 * Every term of the ranking answers one question - which of these does the owner
 * remember clearly enough to speak for the band? - so the list is a shortlist to
 * recognise rather than a leaderboard to read.
 */
function Candidates({
  prompt,
  disabled,
  onPick,
}: {
  prompt: AnchorPrompt;
  disabled: boolean;
  onPick: (film: FilmCard) => Promise<void>;
}) {
  if (prompt.candidates.length === 0) {
    return (
      <p className="muted">
        Nothing you imported landed in this band. You can set it later from any film's page.
      </p>
    );
  }
  return (
    <ul className="candidates">
      {prompt.candidates.map((film) => (
        <li key={film.tmdb_id}>
          <button
            type="button"
            className="candidate"
            disabled={disabled}
            onClick={() => void onPick(film)}
          >
            <Poster title={film.title} path={film.poster_path} size="w154" />
            <span className="candidate-title">{film.title}</span>
            <span className="muted">{releaseYear(film.year)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
