import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api, messageOf, type PlacementStep } from "../api";
import { BandStep, Comparison, Landed } from "../films/steps";

/** Where the flow's two exits lead: back where it was opened from, or Rated by default. */
const EXITS = ["/rated", "/warmup"] as const;

/**
 * The screen that sent the owner here, if it named itself and is one we recognise.
 *
 * An allowlist rather than "any path starting with a slash": this value comes out of the
 * URL bar, and the one thing a redirect target must never be is arbitrary. Two screens
 * open a placement, so two is the whole list.
 */
function useExit(): string {
  const [params] = useSearchParams();
  const asked = params.get("back");
  return EXITS.find((path) => path === asked) ?? EXITS[0];
}

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
 *
 * The steps themselves live in films/steps.tsx, because the settling stream runs the very
 * same ones; what this screen supplies is one film to run them over, and the way out.
 */
export function Place() {
  const { tmdbId } = useParams();
  const id = Number(tmdbId);
  const navigate = useNavigate();
  const exit = useExit();
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

  const leave = (
    <p className="muted place-leave">
      <Link to={exit}>Finish this later</Link> - your answers are kept.
    </p>
  );

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
          footer={leave}
        />
      )}
      {step?.done === false && step.kind === "band" && (
        <BandStep step={step} tmdbId={id} onAnswered={setStep} footer={leave} />
      )}
      {step?.done === true && (
        <Landed
          landed={step}
          tmdbId={id}
          onExtended={setStep}
          primary={
            <button type="button" className="button" onClick={() => void navigate(exit)}>
              Done
            </button>
          }
          footer={<SettleAnother left={step.settle_another} />}
        />
      )}
    </div>
  );
}

/**
 * The one quiet way onward, and only from a settle the owner asked for: a link under the
 * buttons, carrying the count and nothing that reads as a target.
 *
 * It is absent the moment nothing is left, which is the whole of its ending. The settling
 * stream renders nothing here at all - its own "Next film" button is already this offer,
 * and saying it twice would turn a way onward into a chaser (ADR 0011).
 */
function SettleAnother({ left }: { left: number | null }) {
  if (left === null || left === 0) return null;
  return (
    <p className="muted place-leave">
      <Link to="/rated">Settle another</Link> - {left} {left === 1 ? "film is" : "films are"} still
      settling.
    </p>
  );
}
