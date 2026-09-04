import { useState } from "react";
import { useNavigate } from "react-router";

import { api, messageOf } from "../../api";

/**
 * The entry fork: the first screen a new account sees, and the only time it is asked.
 *
 * Full-screen and outside the app frame, like the placement flow, because there is
 * nothing else to do here yet - the navigation would be five destinations with nothing
 * in them. Both branches answer the same question, so both record it and neither is
 * treated as the real one: an owner who starts fresh can import later from Profile, and
 * the warmup they meet then is the same warmup with a different filling.
 *
 * There is no third "skip" branch, deliberately. Starting fresh already leads to a
 * warmup that is skippable at every point, so a skip here would be the same door with
 * a more discouraging sign on it.
 */
export function Welcome() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function choose(next: string) {
    setBusy(true);
    setError(null);
    try {
      await api.enterWarmup();
      void navigate(next);
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <div className="welcome">
      <header className="welcome-head">
        <p className="wordmark" aria-hidden="true">
          Anchor
        </p>
        <h1>Let's find your scale</h1>
        <p className="muted">
          Anchor rates films by comparing them with each other, so it needs a few of yours to
          measure against. Either way in takes a couple of minutes, and you can stop at any
          point.
        </p>
      </header>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="fork">
        <section className="fork-choice card">
          <h2>I have a Letterboxd export</h2>
          <p className="muted">
            Your ratings, watchlist, and diary come across in one go, and your familiar
            half-stars show straight away. Anchor never writes anything back.
          </p>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => void choose("/import")}
          >
            Import my export
          </button>
        </section>

        <section className="fork-choice card">
          <h2>Start fresh</h2>
          <p className="muted">
            Name a few films you know cold, and Anchor builds your scale around them. Nothing
            is imported and nothing is assumed.
          </p>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => void choose("/warmup")}
          >
            Start fresh
          </button>
        </section>
      </div>
    </div>
  );
}
