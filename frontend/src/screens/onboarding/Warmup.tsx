import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";

import {
  api,
  messageOf,
  type PromptState,
  type Warmup as WarmupState,
  type WarmupMark,
} from "../../api";
import { useAsyncAction } from "../../films/useAsyncAction";
import { Designate } from "./Designate";
import { Evidence } from "./Evidence";
import { SeedBacklog } from "./SeedBacklog";

/**
 * The warmup: one skeleton, three phases, filled by whichever way in the owner took.
 *
 * Inside the app frame rather than full-screen, deliberately. "Skippable at every point,
 * the app fully usable throughout" is not a promise worth making in prose while the
 * navigation is hidden - leaving the five destinations right there is the proof of it.
 *
 * All three phases stay open at once. They are not gates on each other: the anchors make
 * the evidence phase more useful, but an owner who skips them can still log films, and a
 * flow that hid phase three behind phase one would be claiming otherwise.
 */
export function Warmup() {
  const navigate = useNavigate();
  const [state, setState] = useState<WarmupState | null>(null);
  const [error, setError] = useState<string | null>(null);

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
        heading="1. Set your anchors"
        blurb="One film per band, as the thing that band means."
        state={state.anchors.state}
        mark="anchors"
        onChanged={setState}
      >
        <Designate phase={state.anchors} fill={state.fill} onChanged={setState} />
      </Phase>

      {/* The one heading the two fills do not share. Everything under it - the body, the
          count, the button - already says which of the two questions is being asked, and
          a heading contradicting all three would be the loudest thing on the step. */}
      <Phase
        heading={
          state.evidence.kind === "comparisons"
            ? "2. Answer a few comparisons"
            : "2. Log a few films you have seen"
        }
        blurb="What the ordering is actually built from."
        state={state.evidence.state}
        mark="evidence"
        onChanged={setState}
      >
        <Evidence phase={state.evidence} fill={state.fill} onChanged={setState} />
      </Phase>

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
  const settled = [state.anchors.state, state.evidence.state, state.backlog.state].every(
    (one) => one !== "todo",
  );

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
