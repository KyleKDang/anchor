import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { api, messageOf, type FilmCard, type PlacementStep } from "../api";
import { BandStep, Comparison, Landed } from "../films/steps";
import { releaseYear } from "../films/tmdb";

/**
 * A settling sitting: the placement flow run over provisional films one after another.
 *
 * The Rated strip's button opens this; the mark on one film opens the ordinary placement
 * screen instead. What the two share is every step in between, which is why the steps
 * themselves live in films/steps.tsx and this screen only supplies the chrome: which film
 * is being worked on, how far in the sitting is, and the two ways out of it.
 *
 * *Nothing about a sitting is stored.* It has no target and no end, so there is nothing to
 * resume: the answers are already in the append-only log, and leaving mid-film costs the
 * owner nothing because the next sitting simply finds that film the narrowest search there
 * is and offers it again. The one thing the server cannot know is which films this sitting
 * has already been through, so that is the only state here, and it dies with the tab -
 * which is exactly what makes "Not this one" free of consequence.
 *
 * *The header names the film the sitting is working on, not the question on screen.* Most
 * questions here are about that film and a few are quiet drift checks about two others,
 * and every one of them sits under the same header - so naming the film says nothing about
 * any single question, and the checks stay indistinguishable from the comparisons around
 * them (rating-system.md).
 */
export function Settling() {
  const [offered, setOffered] = useState<number[]>([]);
  const [film, setFilm] = useState<FilmCard | null | undefined>(undefined);
  const [remaining, setRemaining] = useState(0);
  const [step, setStep] = useState<PlacementStep | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [anchored, setAnchored] = useState(false);
  const [settled, setSettled] = useState<number[]>([]);
  const [answers, setAnswers] = useState(0);

  // The sitting's own count of what it has handed over, kept out of React state because
  // it has to be readable inside the very call that appends to it: two clicks of "Next
  // film" in quick succession must not both send the list as it was before the first.
  const handled = useRef<number[]>([]);

  const pick = useCallback(async () => {
    setError(null);
    setStep(null);
    setFilm(undefined);
    try {
      const next = await api.nextSettling(handled.current);
      setOffered([...handled.current]);
      setFilm(next.film);
      setRemaining(next.remaining);
      if (next.film !== null) setStep(await api.beginPlacement(next.film.tmdb_id));
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  /** Re-open the current film's flow with a ballpark hunch seeding its search. */
  const guess = useCallback(async (tmdbId: number, band: number) => {
    try {
      setStep(await api.beginPlacement(tmdbId, band));
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  /** Take the next film, counting the one on screen as dealt with whatever became of it. */
  const moveOn = useCallback(
    async (current: number) => {
      if (!handled.current.includes(current)) handled.current = [...handled.current, current];
      await pick();
    },
    [pick],
  );

  useEffect(() => {
    void pick();
    // The ballpark hunch seeds a search at the nearest anchor, so with no anchors there is
    // nothing for it to seed and the offer would only be noise.
    api
      .anchors()
      .then((anchors) => setAnchored(!anchors.nudge))
      .catch(() => setAnchored(false));
  }, [pick]);

  // A landing that is no longer provisional is a graduation, which is the only thing the
  // tally counts: an early bail leaves the film on the mark, so it did not settle.
  useEffect(() => {
    if (step?.done !== true || step.provisional) return;
    const landed = step.film.tmdb_id;
    setSettled((already) => (already.includes(landed) ? already : [...already, landed]));
  }, [step]);

  const tally = <Tally settled={settled.length} answers={answers} />;

  /** The ways out of a film: pass on it, or leave the sitting. Grouped, because they read
      as one choice - "not right now" at two different sizes. */
  const exits = (current: number) => (
    <div className="sitting-exits">
      <button type="button" className="link-button" onClick={() => void moveOn(current)}>
        Not this one
      </button>
      {tally}
    </div>
  );

  return (
    <div className="place">
      {film && (
        <SittingHeader film={film} at={offered.length + 1} of={offered.length + remaining} />
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {film === undefined && !error && <p className="muted">Loading…</p>}
      {film === null && !error && <NothingLeft />}
      {/* The way out has to be on screen even when no step is: this flow sits outside the
          app frame, so a failed pick with the leave link tucked inside a step's footer
          would be a screen with no navigation on it at all. */}
      {(film === null || film === undefined || step === null) && tally}

      {film && step?.done === false && step.kind === "comparison" && (
        <Comparison
          step={step}
          tmdbId={film.tmdb_id}
          anchored={anchored}
          onAnswered={setStep}
          onJudged={() => setAnswers((given) => given + 1)}
          onGuess={(band) => void guess(film.tmdb_id, band)}
          footer={exits(film.tmdb_id)}
        />
      )}
      {film && step?.done === false && step.kind === "band" && (
        <BandStep
          step={step}
          tmdbId={film.tmdb_id}
          onAnswered={setStep}
          onJudged={() => setAnswers((given) => given + 1)}
          footer={exits(film.tmdb_id)}
        />
      )}
      {film && step?.done === true && (
        <Landed
          landed={step}
          tmdbId={film.tmdb_id}
          onExtended={setStep}
          primary={<NextFilm onNext={() => void moveOn(film.tmdb_id)} />}
          // No "Settle another" here: the primary button above already is that offer, and
          // saying it twice would turn one way onward into a chaser (ADR 0011).
          footer={tally}
        />
      )}
      {film === null && !error && tally}
    </div>
  );
}

/**
 * Which film the sitting is on, and how far in it is.
 *
 * "About", because it is: settling one film can graduate the films it was compared
 * against, so the total the owner reads is honest only at the moment they read it. Writing
 * it as an approximation is the difference between a progress line and a target, and a
 * sitting has no target.
 */
function SittingHeader({ film, at, of }: { film: FilmCard; at: number; of: number }) {
  return (
    <p className="sitting-header">
      <span className="sitting-film">
        Settling {film.title} <span className="muted">{releaseYear(film.year)}</span>
      </span>
      <span className="muted">
        {at} of about {of}
      </span>
    </p>
  );
}

/**
 * The sitting's tally, sitting beside the way out of it.
 *
 * The only thing a sitting ever says about itself: what it has done, never what is left to
 * do. Graduations only - a film the owner bailed out of is still on the mark, and counting
 * it would be the tally quietly disagreeing with the strip.
 *
 * Leaving is a link rather than a button because it is navigation, and because it has to
 * work whatever state the flow is in - including an error the screen cannot recover from,
 * which is the moment the owner most needs a way out.
 */
function Tally({ settled, answers }: { settled: number; answers: number }) {
  return (
    <p className="muted place-leave">
      <Link to="/rated">Leave settling</Link>{" "}
      <span className="sitting-tally">{summary(settled, answers)}</span>
    </p>
  );
}

function summary(settled: number, answers: number): string {
  if (settled === 0 && answers === 0) return "Nothing settled yet this sitting.";
  const films = `${settled} ${settled === 1 ? "film" : "films"} settled`;
  const given = `${answers} ${answers === 1 ? "answer" : "answers"}`;
  return `${films}, ${given} this sitting.`;
}

/** The landed screen's primary in a sitting: straight on to whatever is next. */
function NextFilm({ onNext }: { onNext: () => void }) {
  return (
    <button type="button" className="button" onClick={onNext}>
      Next film
    </button>
  );
}

/** The sitting's whole ending: it runs out, and says so without a word of praise. */
function NothingLeft() {
  return (
    <header className="place-header">
      <h1>Nothing left to settle</h1>
      <p className="muted">
        Every film in your ordering now rests on your own comparisons. Nothing here is waiting on
        you.
      </p>
    </header>
  );
}
