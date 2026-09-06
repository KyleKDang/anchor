import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type Correction,
  type CriteriaFrequency,
  type Dimension,
  type Picker,
  type Profile as ProfileData,
  type Prose as ProseData,
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
      <ProseSection
        prose={profile?.prose ?? null}
        corrections={profile?.corrections ?? []}
        onCorrections={(corrections) =>
          setProfile((current) => (current === null ? current : { ...current, corrections }))
        }
      />
      <QualitiesSection />
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
          warmup.anchors.state === "todo" && "mark your anchors",
          warmup.rating?.state === "todo" && "rate a few films you have seen",
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
            {profile.evidence.rated_films} film
            {profile.evidence.rated_films === 1 ? "" : "s"} rated, across{" "}
            {profile.evidence.bands_spanned}{" "}
            {profile.evidence.bands_spanned === 1 ? "band" : "bands"}.{" "}
            <Link to="/rated">See the wall</Link>.
          </p>
        </>
      )}
    </section>
  );
}

/**
 * What Anchor thinks the owner likes, in prose, with one ambient line saying how old it is.
 *
 * The section is absent until there is something to show, rather than present and empty:
 * an account that has not earned a regeneration yet is not waiting for one, and a
 * placeholder promising prose "soon" would be the engine narrating background work that
 * ADR 0011 says it never does. The same rule is why the last-updated line is the only
 * metadata here - no version number, no trigger, no refresh control, and nothing that
 * changes while a regeneration is running.
 *
 * The paragraphs are split rather than rendered as one block, because a regeneration
 * writes two or three of them and a wall of text is not what the owner was promised.
 */
function ProseSection({
  prose,
  corrections,
  onCorrections,
}: {
  prose: ProseData | null;
  corrections: Correction[];
  onCorrections: (corrections: Correction[]) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (prose === null) return null;
  const paragraphs = prose.text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0);
  const corrected = new Set(corrections.map((one) => one.claim));

  async function correct(claim: string) {
    setBusy(claim);
    setError(null);
    try {
      onCorrections([...corrections, await api.correctProse(claim)]);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  async function lift(correction: Correction) {
    setBusy(correction.id);
    setError(null);
    try {
      await api.liftCorrection(correction.id);
      onCorrections(corrections.filter((one) => one.id !== correction.id));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="section" aria-labelledby="prose-heading">
      <h2 id="prose-heading">What Anchor thinks you like</h2>
      <div className="prose">
        {paragraphs.map((paragraph, index) => (
          <p key={index} className={`prose-claim${corrected.has(paragraph) ? " corrected" : ""}`}>
            <span>{paragraph}</span>
            {/* One control per paragraph, because a paragraph is the smallest thing a
                regeneration actually writes - splitting it finer would hand the engine
                back a sentence it never composed as a claim of its own. */}
            <button
              type="button"
              className="thumb-down"
              disabled={busy !== null || corrected.has(paragraph)}
              onClick={() => void correct(paragraph)}
              aria-label={`Tell Anchor this is wrong: ${paragraph}`}
            >
              {corrected.has(paragraph) ? "Noted" : "Not right"}
            </button>
          </p>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <p className="muted prose-updated">
        Last updated <time dateTime={prose.generated_at}>{ago(prose.generated_at)}</time>.
      </p>
      {corrections.length > 0 && (
        <div className="corrections">
          <h3>What you have told Anchor is wrong</h3>
          {/* Kept where they were made, and undoable from here: a correction the owner
              cannot find again is one they cannot take back, and these outlive every
              rewrite of the text above by design - which is why they are a list rather
              than a mark on the paragraph they came from. */}
          <ul>
            {corrections.map((correction) => (
              <li key={correction.id}>
                <span className="muted">{correction.claim}</span>
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy !== null}
                  onClick={() => void lift(correction)}
                >
                  Undo
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * The quality picker: a checklist Anchor has already filled in, and the owner's job is
 * to untick what is wrong.
 *
 * Confirm-not-author is the whole design (taste-profile.md), so the section leads with
 * the ticks rather than with a blank form, and says outright that they are a guess while
 * they still are. Nothing here is required and nothing gates on it: an owner who reads
 * the list and closes the screen has lost nothing.
 *
 * It saves behind a button rather than on every tick, which is where it parts company
 * with the frequency control below. A multi-select is answered by the whole set left
 * ticked, so a write per checkbox would send a dozen different answers on the way to the
 * one the owner meant - and each of them would schedule a regeneration.
 */
function QualitiesSection() {
  const [picker, setPicker] = useState<Picker | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .qualities()
      .then((loaded) => {
        setPicker(loaded);
        setChosen(ticksOf(loaded));
      })
      .catch(() => setPicker(null));
  }, []);

  function toggle(id: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
    setSaved(false);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const answered = await api.pickQualities([...chosen]);
      setPicker(answered);
      setChosen(ticksOf(answered));
      setSaved(true);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (custom.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const added = await api.addQuality(custom);
      setPicker(await api.qualities());
      // Ticked here but not saved: adding a quality is naming one, and whether the owner
      // cares about it is still the picker's own question to answer.
      setChosen((current) => new Set([...current, added.id]));
      setCustom("");
      setSaved(false);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  if (picker === null) return null;

  const guessing = !picker.answered && picker.qualities.some((quality) => quality.suggested);
  const dirty = !sameSet(chosen, ticksOf(picker));

  return (
    <section className="section" aria-labelledby="qualities-heading">
      <h2 id="qualities-heading">What you care about</h2>
      <p className="muted">
        {guessing
          ? "Anchor has guessed these from what you have rated. Untick anything that is wrong, and tick anything it missed."
          : "Tick the qualities that matter to you. Anchor weighs them when it describes your taste and when it picks what to recommend."}{" "}
        Entirely optional - Anchor works without it.
      </p>
      <fieldset className="qualities">
        <legend className="visually-hidden">Qualities you care about</legend>
        {picker.qualities.map((quality) => (
          <label key={quality.id} className="quality">
            <input
              type="checkbox"
              checked={chosen.has(quality.id)}
              onChange={() => toggle(quality.id)}
            />
            <span>{quality.name}</span>
          </label>
        ))}
      </fieldset>
      <form className="quality-add" onSubmit={(event) => void add(event)}>
        <label className="field">
          <span>Something else you care about</span>
          <input
            type="text"
            name="quality"
            maxLength={64}
            placeholder="Worldbuilding"
            value={custom}
            onChange={(event) => setCustom(event.target.value)}
          />
        </label>
        <button type="submit" className="button secondary" disabled={busy || custom.trim() === ""}>
          Add
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="quality-save">
        {/* Pressable when there is something to say, which includes agreeing with the
            guess exactly as it stands - that is the confirming this whole control exists
            for, and a Save that only woke up on disagreement would refuse the commonest
            answer. An empty checklist nobody has touched is the one case with nothing to
            record, and stays disabled rather than letting a stray click answer "none of
            these" and silence the guessing for good. */}
        <button
          type="button"
          className="button"
          disabled={busy || (!dirty && (picker.answered || chosen.size === 0))}
          onClick={() => void save()}
        >
          Save
        </button>
        {saved && !dirty && <span className="muted">Saved.</span>}
      </div>
    </section>
  );
}

/** What the picker currently shows ticked: the owner's answer, or Anchor's guess. */
function ticksOf(picker: Picker): Set<string> {
  return new Set(
    picker.qualities.filter((quality) => quality.checked).map((quality) => quality.id),
  );
}

function sameSet(a: Set<string>, b: Set<string>): boolean {
  return a.size === b.size && [...a].every((one) => b.has(one));
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 60 * 60],
  ["month", 30 * 24 * 60 * 60],
  ["week", 7 * 24 * 60 * 60],
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
];

/**
 * How long ago, in the coarsest unit that still says something: "3 days ago".
 *
 * Deliberately vague. The owner is being told the description is current enough to trust,
 * not audited on when a job ran, and a timestamp to the minute would invite reading the
 * engine's schedule off a line that exists only to be glanced at.
 */
function ago(timestamp: string): string {
  const seconds = (Date.parse(timestamp) - Date.now()) / 1000;
  const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
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

/** Both dimensions are counts, so both read as counts. */
function format(_dimension: Dimension, value: number): string {
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
