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
 * Phase 1: mark the films you know cold, one band at a time.
 *
 * One prompt at a time rather than ten at once. The five whole stars come in the order
 * they are easiest to answer - best, worst, middle, then the two that only become
 * findable once their neighbours exist - and asking them all together would throw that
 * away and turn a two-minute flow into a form.
 *
 * The app never marks an anchor. Everything here offers, ranks, and gets out of the way;
 * the write happens on the owner's own tap and nowhere else (ADR 0013).
 */
export function Designate({
  phase,
  fill,
  from,
  onChanged,
}: {
  phase: AnchorPhase;
  fill: Warmup["fill"];
  /** The band the picker was opened from, for a film rated mid-prompt and now back. */
  from: number | null;
  onChanged: (warmup: Warmup) => void;
}) {
  // Opened straight onto a half-star band: the owner is already past the continuation's
  // door, so it is open behind them rather than a thing to ask about again.
  const [continuing, setContinuing] = useState(from !== null && isHalfStar(from));
  // The band the run is standing on, once the owner has answered it. Any number may be
  // marked per band, so a mark does not carry the run on by itself: the second anchor is
  // in the same list the first came from, and advancing on the first would take that
  // list away to make a point the owner never asked for.
  const [held, setHeld] = useState<number | null>(from);
  const queue = continuing ? [...phase.prompts, ...phase.continuation] : phase.prompts;
  const open = queue.find((prompt) => prompt.state === "todo") ?? null;
  const current = queue.find((prompt) => prompt.band === held) ?? open;

  return (
    <>
      <ol className="anchor-track" aria-label="Bands to anchor">
        {queue.map((prompt) => (
          <TrackMark key={prompt.band} prompt={prompt} current={prompt === current} />
        ))}
      </ol>

      {current === null ? (
        <Finished phase={phase} continuing={continuing} onContinue={() => setContinuing(true)} />
      ) : (
        <Prompt
          prompt={current}
          fill={fill}
          position={queue.indexOf(current) + 1}
          total={queue.length}
          onMarked={() => setHeld(current.band)}
          onNext={() => setHeld(null)}
          onChanged={onChanged}
        />
      )}
    </>
  );
}

/** A band the continuation holds rather than the five the run prompts for outright. */
function isHalfStar(band: number): boolean {
  return band % 1 !== 0;
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
        {prompt.state === "done" ? "marked" : prompt.state === "skipped" ? "skipped" : "to do"}
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
  const anchored = [...phase.prompts, ...phase.continuation].filter((one) => one.marked.length > 0);
  const more = phase.continuation.some((one) => one.state === "todo");

  return (
    <>
      {anchored.length === 0 ? (
        <p className="muted">
          No anchors yet. Your ratings work exactly the same without them; anchors are what the band
          picker shows you when you rate, so you choose against your own references.
        </p>
      ) : (
        <ul className="anchor-set">
          {anchored.map((prompt) => (
            <li key={prompt.band} className="anchor-set-item">
              <span className="band-stars" aria-hidden="true">
                {stars(prompt.band)}
              </span>
              <span className="band-value">{prompt.band.toFixed(1)}</span>
              <span className="film-title">
                {prompt.marked.map((film) => film.title).join(", ")}
              </span>
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
  onMarked,
  onNext,
  onChanged,
}: {
  prompt: AnchorPrompt;
  fill: Warmup["fill"];
  position: number;
  total: number;
  /** A mark landed: hold the run here, because the band may take another. */
  onMarked: () => void;
  /** The owner is done with this band, marked or not: carry the run on. */
  onNext: () => void;
  onChanged: (warmup: Warmup) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();
  const marked = prompt.marked.length > 0;

  /**
   * Marking a film the owner has never rated: rate it first, then mark it.
   *
   * There is no separate designation flow any more - rate a film, mark it, and the
   * band's pool exists (onboarding-and-import.md). So a film with no rating goes to the
   * picker, which is where the owner says what band it is; a film already rated is one
   * tap away from being an anchor.
   */
  async function markFresh(film: SearchResult) {
    await run(async () => {
      if (film.state === "rated") {
        await api.markAnchor(film.tmdb_id);
        onMarked();
        onChanged(await api.warmup());
        return;
      }
      if (film.state === null || film.state === "backlog") {
        await api.markWatched(film.tmdb_id, "later");
      }
      // The band rides to the picker and back, so the film lands and the owner returns
      // to the prompt that sent them - which is where the film they just rated is.
      await navigate(`${placePath(film.tmdb_id)}?back=/warmup&band=${prompt.band.toFixed(1)}`);
    });
  }

  /** A candidate is drawn from the owner's own library, so it is already rated. */
  async function markCandidate(film: FilmCard) {
    await run(async () => {
      await api.markAnchor(film.tmdb_id);
      onMarked();
      onChanged(await api.warmup());
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
        Pick one you know cold. It is what the band picker shows you when you rate, so a film you
        are sure about is worth more than a film you love - and you can mark as many as you like.
      </p>

      {/* What the band already holds, so a second mark is an addition to something the
          owner can see rather than a tap into the dark. */}
      {marked && (
        <p className="muted">
          Marked: {prompt.marked.map((film) => film.title).join(", ")}. Mark another below, or move
          on.
        </p>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {/* The owner's own films in this band come first wherever there are any: on the
          import fill that is the whole of the step, and on the fresh fill it is the film
          they just rated through the picker, which is what they came back to mark.
          Search stays on the fresh fill because there it is the way in, not a fallback. */}
      <Candidates prompt={prompt} disabled={busy} onPick={markCandidate} empty={fill !== "fresh"} />
      {fill === "fresh" && (
        <FilmPicker
          label={`Find your ${prompt.band.toFixed(1)}`}
          action={`This is my ${prompt.band.toFixed(1)}`}
          browse
          disabled={busy}
          onPick={markFresh}
        />
      )}

      {/* One control, and which one it is says what leaving this band would mean. An
          unanswered band is skipped - the owner is saying stop asking - and an answered
          one is simply left, because the question has been answered as fully as they
          want it answered. */}
      <p className="prompt-skip">
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() =>
            marked
              ? onNext()
              : void run(async () => {
                  onNext();
                  onChanged(await api.skipWarmup("anchors", prompt.band));
                })
          }
        >
          {marked ? "Next band" : "Skip this band"}
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
  empty,
}: {
  prompt: AnchorPrompt;
  disabled: boolean;
  onPick: (film: FilmCard) => Promise<void>;
  /** Say so when there is nothing to offer, rather than leaving a gap.
   *
   * True on the import fill, where an empty band is news: the export put nothing here.
   * False on the fresh fill, where search is right underneath and an empty library is
   * the ordinary state rather than something to remark on. */
  empty: boolean;
}) {
  if (prompt.candidates.length === 0) {
    if (!empty) return null;
    return (
      <p className="muted">
        Nothing you imported landed in this band. You can mark one later from any film's page.
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
