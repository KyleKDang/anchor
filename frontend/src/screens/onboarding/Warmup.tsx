import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import {
  api,
  messageOf,
  type PromptState,
  type RatingPhase as RatingPhaseState,
  type Warmup as WarmupState,
  type WarmupMark,
} from "../../api";
import { useAsyncAction } from "../../films/useAsyncAction";
import { Designate } from "./Designate";
import { LookOverTheWall } from "./LookOverTheWall";
import { SeedBacklog } from "./SeedBacklog";

/**
 * The warmup: one skeleton, filled by whichever way in the owner took.
 *
 * Both fills have three phases and only the middle one differs: the fresh fill rates a
 * few more films, and the import fill looks over the wall its export just built, which
 * is edit mode on the Rated screen (ADR 0013 removed the settling step that stood there).
 *
 * Inside the app frame rather than full-screen, deliberately. "Skippable at every point,
 * the app fully usable throughout" is not a promise worth making in prose while the
 * navigation is hidden - leaving the five destinations right there is the proof of it.
 *
 * Every phase stays open at once. They are not gates on each other: the anchors make the
 * rating phase more useful, but an owner who skips them can still rate films, and a flow
 * that hid the last phase behind the first would be claiming otherwise.
 */
export function Warmup() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [state, setState] = useState<WarmupState | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The band the picker was opened from, so a film rated mid-prompt comes back to the
  // prompt that sent it rather than to wherever the run would otherwise have moved on to.
  const from = params.has("band") ? Number(params.get("band")) : null;

  useEffect(() => {
    api
      .warmup()
      .then((warmup) => {
        // Never answered the fork: this is the same account arriving one screen early.
        if (warmup.fork) void navigate("/welcome", { replace: true });
        else setState(warmup);
      })
      .catch((caught: unknown) => setError(messageOf(caught)));
  }, [navigate]);

  if (error !== null) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (state === null) return <p className="muted">Loading…</p>;

  return (
    <div className="warmup">
      <header className="warmup-head">
        <h1>Warm up</h1>
        <p className="muted">
          Three steps, a couple of minutes. Skip any of them - Anchor works either way, and
          everything here can be done later from your own screens.
        </p>
      </header>

      <Phase
        heading="1. Mark your anchors"
        blurb="The films you know cold, as the references you rate against."
        state={state.anchors.state}
        mark="anchors"
        onChanged={setState}
      >
        <Designate phase={state.anchors} fill={state.fill} from={from} onChanged={setState} />
      </Phase>

      {/* The one step that differs between the fills. An owner who imported already has
          ratings, so asking for more would ask for the thing they arrived holding; what
          they have not seen is the ordering those ratings made. */}
      {state.rating !== null && (
        <Phase
          heading="2. Rate a few films you have seen"
          blurb="What the ordering is actually built from."
          state={state.rating.state}
          mark="rating"
          onChanged={setState}
        >
          <RatingPhase phase={state.rating} />
        </Phase>
      )}

      {state.wall !== null && (
        <Phase
          heading="2. Look over the wall"
          blurb="The ordering your export made, and how to change it."
          state={state.wall.state}
          mark="wall"
          onChanged={setState}
        >
          <LookOverTheWall phase={state.wall} />
        </Phase>
      )}

      <Phase
        heading="3. Fill your backlog"
        blurb="Something to watch next, usable from minute one."
        state={state.backlog.state}
        mark="backlog"
        onChanged={setState}
      >
        <SeedBacklog phase={state.backlog} fill={state.fill} onChanged={setState} />
      </Phase>

      <Done state={state} onChanged={setState} />
    </div>
  );
}

/**
 * The fresh fill's middle step: "rate ~5 films you have seen".
 *
 * A count and nothing else, because the act itself happens elsewhere - search for a film,
 * mark it watched, rate it - and a button here would be a third door into a flow that
 * already has two. The target is advisory: it says when the phase stops asking, never
 * when the owner is allowed to leave.
 */
function RatingPhase({ phase }: { phase: RatingPhaseState }) {
  return (
    <>
      <p className="muted">
        {phase.rated} of about {phase.target} so far. Find a film on{" "}
        <Link to="/search">Search</Link>, mark it watched, and rate it.
      </p>
      {phase.rated === 0 && (
        <p className="muted">
          Every rating is one tap on the band picker - there is nothing to work through.
        </p>
      )}
    </>
  );
}

const MARK_LABEL: Record<PromptState, string | null> = {
  todo: null,
  done: "done",
  skipped: "skipped",
};

/**
 * One phase, open unless the owner put it away.
 *
 * A skipped phase collapses to its heading and an undo, rather than vanishing: the owner
 * skipped a question, not the existence of the question, and an offer they can no longer
 * find is a worse answer than one sitting quietly closed.
 */
function Phase({
  heading,
  blurb,
  state,
  mark,
  onChanged,
  children,
}: {
  heading: string;
  blurb: string;
  state: PromptState;
  mark: WarmupMark;
  onChanged: (warmup: WarmupState) => void;
  children: ReactNode;
}) {
  const { busy, error, run } = useAsyncAction();

  return (
    <section className="section warmup-phase" data-state={state}>
      {/* The phase-level skip lives on the heading row rather than under the body: a
          step's own skip sits at the bottom of that step, and two skips stacked there
          would be two identical links meaning different amounts. */}
      <h2 className="warmup-phase-head">
        <span>{heading}</span>
        {MARK_LABEL[state] !== null && <span className="stage-mark">{MARK_LABEL[state]}</span>}
        {state === "todo" && (
          <button
            type="button"
            className="link-button warmup-phase-skip"
            disabled={busy}
            onClick={() => void run(async () => onChanged(await api.skipWarmup(mark)))}
          >
            Skip
            <span className="visually-hidden"> {heading}</span>
          </button>
        )}
      </h2>
      {error !== null && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {/* The blurb is the closed state's whole explanation, so it only shows there: open,
          the body says the same thing at length and the two together read as a stutter. */}
      {state === "skipped" ? (
        <p className="muted">
          {blurb} <Link to="/profile">Still available whenever you want it.</Link>
        </p>
      ) : (
        children
      )}
    </section>
  );
}

/** Putting the whole thing away, which is the last thing the warmup is ever asked to do. */
function Done({
  state,
  onChanged,
}: {
  state: WarmupState;
  onChanged: (warmup: WarmupState) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();
  const settled = [
    state.anchors.state,
    state.rating?.state ?? state.wall?.state ?? "done",
    state.backlog.state,
  ].every((one) => one !== "todo");

  async function finish() {
    await run(async () => {
      onChanged(await api.dismissWarmup());
      await navigate("/rated");
    });
  }

  return (
    <>
      {error !== null && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="actions warmup-done">
        <button type="button" className="button" disabled={busy} onClick={() => void finish()}>
          {settled ? "Take me in" : "I'm done for now"}
        </button>
        <span className="muted">Nothing here is a gate. You can come back from Profile.</span>
      </div>
    </>
  );
}
