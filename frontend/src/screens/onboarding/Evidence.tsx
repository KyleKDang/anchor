import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type EvidencePhase,
  type Readiness,
  type Verdict,
  type Warmup,
  type WarmupStep,
} from "../../api";
import { Plot } from "../../films/Plot";
import { Poster } from "../../films/Poster";
import { releaseYear } from "../../films/tmdb";

/**
 * What each readiness state turns on, in the owner's terms rather than the engine's.
 *
 * The words are the placement's, not Profile's: this is the same moment Place announces
 * - the answer that crossed the bar, on the screen of the act that earned it - and one
 * unlock told two ways would read as two different unlocks. Profile's copy is a standing
 * readout of where the account is, which is a different sentence for a different job.
 */
const UNLOCKED: Record<Readiness, string> = {
  cold: "",
  forming: "That was enough to go on: Anchor can suggest films you have never tracked now.",
  ready: "That was enough to go on. Your watchlist is ranked from here: Anchor puts what you are most likely to love next at the top.",
};

/**
 * Phase 2: give the ordering something to work with, whichever way the fill asks.
 *
 * The two fills ask differently because they start differently. An imported account has
 * a library and no order inside its bands, so the question is which of two films is
 * better. A fresh account has almost nothing, so the question is what else the owner has
 * seen - and each of those placements is a handful of comparisons anyway.
 */
export function Evidence({
  phase,
  fill,
  onChanged,
}: {
  phase: EvidencePhase;
  fill: Warmup["fill"];
  onChanged: (warmup: Warmup) => void;
}) {
  return fill === "imported" ? (
    <Comparisons onChanged={onChanged} />
  ) : (
    <Placements phase={phase} />
  );
}

/**
 * The import fill: comparisons inside the tie-groups the export could not order.
 *
 * The count comes off the step rather than off the phase the parent already holds. Both
 * say the same thing, but the step's is the one that moved when the owner answered, and
 * two readings of one number are exactly how a stale figure gets on screen.
 */
function Comparisons({ onChanged }: { onChanged: (warmup: Warmup) => void }) {
  const [step, setStep] = useState<WarmupStep | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStep(await api.warmupComparison());
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function answer(verdict: Verdict) {
    if (step === null || step.done) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.answerWarmupComparison(step.a.tmdb_id, step.b.tmdb_id, verdict);
      setStep(next);
      onChanged(await api.warmup());
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="muted">
        Your export knew what each film scored, but not how they rank against each other.
        These are the pairs Anchor learns the most from.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {step !== null && (
        <p className="prompt-step muted">
          {step.answered} of about {step.target} answered
        </p>
      )}
      {/* One line, on the step the owner is already looking at, and only when this very
          answer crossed the bar. Nothing else marks it (ADR 0011). */}
      {step?.unlocked != null && step.unlocked !== "cold" && (
        <p className="notice" role="status">
          {UNLOCKED[step.unlocked]}
        </p>
      )}
      {step?.done === false && (
        <>
          <div className="warmup-pair">
            <ComparisonCard
              film={step.a}
              label="Better"
              disabled={busy}
              onPick={() => void answer("a")}
            />
            <ComparisonCard
              film={step.b}
              label="Better"
              disabled={busy}
              onPick={() => void answer("b")}
            />
          </div>
          <div className="actions place-answers">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={() => void answer("tied")}
            >
              Too close to call
            </button>
            <button
              type="button"
              className="link-button"
              disabled={busy}
              onClick={() => void answer("skip")}
            >
              Skip this pair
            </button>
          </div>
        </>
      )}
      {step?.done === true && (
        <p className="muted">
          {step.answered === 0
            ? "Nothing left to compare here - your imported bands are already in order."
            : `That is ${step.answered} comparison${step.answered === 1 ? "" : "s"} in the bank. Anchor keeps asking, quietly, as you use it.`}
        </p>
      )}
      {step === null && !error && <p className="muted">Loading…</p>}
    </>
  );
}

/**
 * One side of a warmup comparison.
 *
 * No rating anywhere on it, and not merely hidden: the server never sends one, so the
 * owner answers on the pure which-is-better instinct rather than on what the import
 * happened to say two films scored.
 */
function ComparisonCard({
  film,
  label,
  disabled,
  onPick,
}: {
  film: { tmdb_id: number; title: string; year: number | null; poster_path: string | null; overview: string };
  label: string;
  disabled: boolean;
  onPick: () => void;
}) {
  return (
    <div className="place-card">
      <Poster title={film.title} path={film.poster_path} size="w342" />
      <h3>{film.title}</h3>
      <p className="muted">{releaseYear(film.year)}</p>
      <Plot overview={film.overview} />
      <button type="button" className="button" disabled={disabled} onClick={onPick}>
        {label}
        <span className="visually-hidden">: {film.title}</span>
      </button>
    </div>
  );
}

/** The fresh fill: ordinary placements, which is all this phase ever was. */
function Placements({ phase }: { phase: EvidencePhase }) {
  const left = Math.max(0, phase.target - phase.answered);

  return (
    <>
      {/* The heading above already says what to do, so this says why it is worth doing. */}
      <p className="muted">
        Each one is a handful of quick comparisons against the anchors you just set, and that
        is what turns positions into ratings.
      </p>
      <p className="prompt-step muted">
        {phase.answered} of about {phase.target} logged
      </p>
      {left > 0 ? (
        <p>
          <Link className="button secondary" to="/search">
            Find a film you have seen
          </Link>
        </p>
      ) : (
        <p className="muted">
          That is enough to work with. Keep logging whenever you like - the ordering gets
          sharper with every one.
        </p>
      )}
    </>
  );
}
