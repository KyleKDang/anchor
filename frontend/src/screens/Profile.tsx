import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type CriteriaFrequency,
  type Dimension,
  type Profile as ProfileData,
  type Readiness,
  type Stage,
  type Threshold,
  type Warmup as WarmupData,
} from "../api";
import { useAuth } from "../auth";
import { Letterboxd } from "./import/Letterboxd";

export function Profile() {
  // One fetch for the whole screen: two sections read the same payload, and asking the
  // server twice for it would let them disagree about what the account currently is.
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
    <>
      <h1>Profile</h1>
      <WarmupSection />
      <ReadinessSection profile={profile} error={error} />
      <CriteriaSection frequency={profile?.criteria_frequency ?? null} />
      <Letterboxd />
      <AccountSection />
      <TmdbAttribution />
    </>
  );
}

/**
 * The warmup's home once the owner has left it: a line, and a way back.
 *
 * Ambient by ADR 0011's rules - it sits here and is mentioned nowhere else, it counts
 * nothing, and it goes quiet the moment there is nothing left to offer. A dismissed
 * warmup still shows, because dismissing is putting a thing away rather than destroying
 * it, and an offer the owner cannot find again is worse than one sitting quietly here.
 */
function WarmupSection() {
  const [warmup, setWarmup] = useState<WarmupData | null>(null);

  useEffect(() => {
    api
      .warmup()
      .then(setWarmup)
      .catch(() => setWarmup(null));
  }, []);

  const left =
    warmup === null
      ? []
      : [
          warmup.anchors.state === "todo" && "set your anchors",
          warmup.evidence.state === "todo" &&
            (warmup.evidence.kind === "comparisons"
              ? "answer a few comparisons"
              : "log a few films you have seen"),
          warmup.backlog.state === "todo" && "fill your backlog",
        ].filter((one): one is string => one !== false);
  if (warmup === null || left.length === 0) return null;

  return (
    <section className="section" aria-labelledby="warmup-heading">
      <h2 id="warmup-heading">Warm up</h2>
      <p className="muted">
        Still open: {left.join(", ")}. None of it is required - it just makes Anchor's ratings
        arrive sooner.
      </p>
      <Link className="button secondary" to="/warmup">
        Pick up the warmup
      </Link>
    </section>
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
function ReadinessSection({
  profile,
  error,
}: {
  profile: ProfileData | null;
  error: string | null;
}) {
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
            {shortOnJudgment(profile.stages) && (
              <>
                {" "}
                <Link to="/rated#settling">Settle some films</Link> to answer more.
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}

/**
 * How often the bonus question appears, in the owner's terms rather than the engine's.
 *
 * The options say what the owner will experience, not what the server computes: "Adaptive"
 * is described by what it does to them, and "Never" is spelled out as complete rather than
 * quiet, because a control that turns out to have kept asking is worse than no control.
 * The gaps behind each level are the engine's business and are deliberately not numbers
 * here - a "once every 24 comparisons" label would be a promise about tuning.
 */
const FREQUENCY_OPTIONS: { value: CriteriaFrequency; label: string; hint: string }[] = [
  {
    value: "adaptive",
    label: "Adaptive",
    hint: "Asks more when you answer, and backs off when you don't.",
  },
  { value: "often", label: "Often", hint: "After most placements." },
  { value: "sometimes", label: "Sometimes", hint: "Every few placements." },
  { value: "rarely", label: "Rarely", hint: "Occasionally, and never twice in a row." },
  { value: "off", label: "Never", hint: "No bonus questions at all, and none recorded." },
];

/**
 * The criteria-question frequency control and its off switch.
 *
 * It saves on change rather than behind a Save button: there is one value, it is
 * reversible, and a settings screen that needs confirming to turn something off is a
 * settings screen people do not use. The choice moves optimistically so the radio never
 * lags the tap, and falls back to what the server last confirmed if the write fails.
 */
function CriteriaSection({ frequency }: { frequency: CriteriaFrequency | null }) {
  const [chosen, setChosen] = useState<CriteriaFrequency | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = chosen ?? frequency;

  async function choose(value: CriteriaFrequency) {
    const previous = current;
    setChosen(value);
    setError(null);
    try {
      await api.setCriteriaFrequency(value);
    } catch (caught) {
      setChosen(previous);
      setError(messageOf(caught));
    }
  }

  return (
    <section className="section" aria-labelledby="bonus-heading">
      <h2 id="bonus-heading">Bonus questions</h2>
      <p className="muted">
        After a placement Anchor sometimes asks one extra question about the films you just
        compared - which had the better screenplay, say. Answering is always optional, and
        the answers shape what Anchor recommends without ever moving your ordering.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {/* Nothing until the setting is known: a radio group rendered greyed out with no
          option selected reads as a control that is broken rather than one still loading. */}
      {current !== null && (
        <fieldset className="choices">
          <legend className="visually-hidden">How often to ask</legend>
          {FREQUENCY_OPTIONS.map((option) => (
            <label key={option.value} className="choice">
              <input
                type="radio"
                name="criteria-frequency"
                value={option.value}
                checked={current === option.value}
                onChange={() => void choose(option.value)}
              />
              <span className="choice-label">{option.label}</span>
              <span className="muted">{option.hint}</span>
            </label>
          ))}
        </fieldset>
      )}
    </section>
  );
}

/**
 * Whether what this account is short of is the owner's own judgment, rather than films.
 *
 * The two bars that measure it are the two halves of one shortfall - how much of the
 * library rests on comparisons, and how many of them the owner has actually answered - and
 * settling is the one thing in the app that moves either. Where that is what is missing,
 * the readiness section links into the Rated strip rather than dead-ending (surfacing.md).
 */
function shortOnJudgment(stages: Stage[]): boolean {
  return stages.some((stage) =>
    stage.thresholds.some(
      (threshold) =>
        (threshold.dimension === "settled_share" ||
          threshold.dimension === "comparisons_per_film") &&
        threshold.have < threshold.need,
    ),
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
