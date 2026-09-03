import { useEffect, useState, type FormEvent } from "react";

import {
  api,
  messageOf,
  type Dimension,
  type Profile as ProfileData,
  type Readiness,
  type Stage,
  type Threshold,
} from "../api";
import { useAuth } from "../auth";

export function Profile() {
  return (
    <>
      <h1>Profile</h1>
      <ReadinessSection />
      <AccountSection />
      <TmdbAttribution />
    </>
  );
}

/** What each state gives the owner, in their terms rather than the engine's. */
const UNLOCKS: Record<Readiness, string> = {
  cold: "Anchor is still learning. Discovery and the ranked watchlist stay off until it has enough to go on.",
  forming: "Discovery is on: Anchor can suggest films you have never tracked.",
  ready: "The ranked watchlist is on: your backlog is ordered by what you are most likely to love next.",
};

const STATE_LABEL: Record<Readiness, string> = {
  cold: "Cold",
  forming: "Forming",
  ready: "Ready",
};

const DIMENSION_LABEL: Record<Dimension, string> = {
  rated_films: "Films rated",
  bands_spanned: "Half-star bands your ratings span",
  settled_share: "Rated by your own comparisons",
  comparisons_per_film: "Comparisons answered per film",
};

/**
 * How ready the taste profile is, said plainly.
 *
 * Readiness is derived from evidence and has no time component, so the honest thing to
 * show is the arithmetic: what each state needs, and what this account has against it.
 * "Eleven more films" is something the owner can act on; "not yet" is not.
 *
 * Nothing here is rating-shaped, and nothing can become so: these are counts (ADR 0005).
 */
function ReadinessSection() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setProfile(await api.profile());
        setError(null);
      } catch (caught) {
        setError(messageOf(caught));
      }
    })();
  }, []);

  return (
    <section className="section" aria-labelledby="readiness-heading">
      <h2 id="readiness-heading">Taste profile</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {profile !== null && (
        <>
          {/* A chip rather than a big word: the state is a value, and set as a heading it
              would outrank the section heading above it. */}
          <p className="chip">{STATE_LABEL[profile.readiness]}</p>
          <p className="muted">{UNLOCKS[profile.readiness]}</p>
          <ol className="stages">
            {profile.stages.map((stage) => (
              <StageRow key={stage.state} stage={stage} />
            ))}
          </ol>
          <p className="muted">
            {profile.evidence.explicit_comparisons} comparison
            {profile.evidence.explicit_comparisons === 1 ? "" : "s"} answered so far.
          </p>
        </>
      )}
    </section>
  );
}

function StageRow({ stage }: { stage: Stage }) {
  return (
    <li className={`stage card${stage.reached ? " reached" : ""}`}>
      <p className="stage-name">
        {STATE_LABEL[stage.state]}
        <span className="stage-mark">{stage.reached ? "reached" : "not yet"}</span>
      </p>
      <p className="muted">{UNLOCKS[stage.state]}</p>
      <ul className="bars">
        {stage.thresholds.map((threshold) => (
          <Bar key={threshold.dimension} threshold={threshold} />
        ))}
      </ul>
    </li>
  );
}

/**
 * One bar, with the bar it has to clear written into its own label.
 *
 * The two numbers are deliberately not shown side by side: "100% / 50%" reads as a
 * fraction of percentages rather than as a value against a target, so the target lives
 * in the label and the figure column carries only where the account actually stands.
 */
function Bar({ threshold }: { threshold: Threshold }) {
  const have = format(threshold.dimension, threshold.have);
  const need = format(threshold.dimension, threshold.need);
  const filled = Math.min(1, threshold.need === 0 ? 1 : threshold.have / threshold.need);
  return (
    <li className="bar">
      <span className="bar-label">
        {DIMENSION_LABEL[threshold.dimension]} <span className="bar-need">(needs {need})</span>
      </span>
      <span className="bar-track" role="img" aria-label={`${have} of ${need}`}>
        <span className="bar-fill" style={{ inlineSize: `${filled * 100}%` }} />
      </span>
      <span className="bar-figures" aria-hidden="true">
        {have}
      </span>
    </li>
  );
}

/** A share reads as a percentage; a rate keeps one decimal; a count is just a count. */
function format(dimension: Dimension, value: number): string {
  if (dimension === "settled_share") return `${Math.round(value * 100)}%`;
  if (dimension === "comparisons_per_film") return value.toFixed(1);
  return String(value);
}

function AccountSection() {
  const { account, logOut, accountDeleted } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleDelete(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.deleteAccount(password);
      accountDeleted();
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <section className="section" aria-labelledby="account-heading">
      <h2 id="account-heading">Account</h2>
      <p>
        Logged in as <strong>{account?.email}</strong>.
      </p>
      <button type="button" className="button secondary" onClick={() => void logOut()}>
        Log out
      </button>

      <form onSubmit={handleDelete} className="form danger-zone" aria-labelledby="delete-heading">
        <h3 id="delete-heading">Delete account</h3>
        <p className="muted">
          This deletes your account and everything in it: ratings, comparisons, watchlist, taste
          profile. There is no undo.
        </p>
        <label className="field">
          <span>Confirm your password</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="button danger" disabled={busy}>
          Delete account
        </button>
      </form>
    </section>
  );
}

/**
 * The attribution TMDB's terms require wherever their data is used (ADR 0003): their
 * logo, and the notice that they have not endorsed this. It is not dismissible.
 */
function TmdbAttribution() {
  return (
    <section className="section attribution" aria-labelledby="tmdb-heading">
      <h2 id="tmdb-heading">Film data</h2>
      <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer noopener">
        <img className="tmdb-logo" src="/tmdb.svg" alt="The Movie Database (TMDB)" />
      </a>
      <p className="muted">
        This product uses the TMDB API but is not endorsed, certified, or otherwise approved by
        TMDB.
      </p>
    </section>
  );
}
