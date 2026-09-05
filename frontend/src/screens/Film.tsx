import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import {
  BANDS,
  api,
  messageOf,
  type DriftFlag,
  type FilmDetail,
  type KeepOpponent,
} from "../api";
import { AnchorBadge, Band, ProvisionalMark } from "../films/Band";
import { MarkWatched } from "../films/MarkWatched";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { StateFlag } from "../films/StateFlag";
import { placePath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The film page, reached by tapping a film anywhere. It is not a destination of its own.
 *
 * The page shifts with the film's state: untracked and in-backlog offer the backlog and
 * the watch, watched-unrated carries the seen marker and place-it-now, and rated shows
 * its band, whether it anchors that band, the designation control, the rewatch, and the
 * drift flag with its resolution options where one is open. Pin and veto arrive with the
 * ranked tier; the judgment history with the criteria system.
 */
export function Film() {
  const { tmdbId } = useParams();
  const id = Number(tmdbId);
  const [film, setFilm] = useState<FilmDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setFilm(null);
    setError(null);
    api
      .film(id)
      .then((loaded) => !cancelled && setFilm(loaded))
      .catch((caught: unknown) => !cancelled && setError(messageOf(caught)));
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return (
      <>
        <BackToSearch />
        <p className="error" role="alert">
          {error}
        </p>
      </>
    );
  }
  if (film === null) return <BackToSearch />;
  return <FilmPage film={film} onChange={setFilm} />;
}

function FilmPage({ film, onChange }: { film: FilmDetail; onChange: (film: FilmDetail) => void }) {
  const { busy, error, run } = useAsyncAction();

  return (
    <>
      <BackToSearch />
      <article className="film-page">
        <Poster title={film.title} path={film.poster_path} size="w342" />
        {/* Identity above the fold beside the poster; everything the owner can do with the
            film goes below, where a phone can give it the full width. */}
        <div className="film-page-head">
          <h1>{film.title}</h1>
          <p className="film-row-meta">
            <span className="muted">
              {[releaseYear(film.year), runtime(film.runtime)].join(" · ")}
            </span>
            {/* The band gets its own panel below for a rated film, so the flag says
                the state and leaves the value to it. */}
            <StateFlag state={film.state} rating={film.state === "rated" ? null : film.rating} />
          </p>
          {film.genres.length > 0 && (
            <ul className="genres">
              {film.genres.map((genre) => (
                <li className="chip" key={genre}>
                  {genre}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="film-page-body">
          {film.directors.length > 0 && (
            <p>
              <strong>Directed by</strong> {film.directors.join(", ")}
            </p>
          )}
          {film.cast.length > 0 && (
            <p>
              <strong>Starring</strong> {film.cast.join(", ")}
            </p>
          )}
          <Plot overview={film.overview} />

          <div className="actions film-actions">
            {film.state === null && (
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => void run(async () => onChange(await api.addToBacklog(film.tmdb_id)))}
              >
                Add to backlog
              </button>
            )}
            {film.state === "backlog" && (
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.removeFromBacklog(film.tmdb_id);
                    onChange({ ...film, state: null });
                  })
                }
              >
                Remove from backlog
              </button>
            )}
            {(film.state === null || film.state === "backlog") && (
              <MarkWatched
                tmdbId={film.tmdb_id}
                label="I watched this"
                onLater={() =>
                  onChange({ ...film, state: "watched_unrated", rate_later: true })
                }
              />
            )}
            {film.state === "watched_unrated" && (
              <Link className="button" to={placePath(film.tmdb_id)}>
                Place it now
              </Link>
            )}
            {film.state === "watched_unrated" && film.rate_later && (
              <button
                type="button"
                className="link-button"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.leaveRateLater(film.tmdb_id);
                    onChange({ ...film, rate_later: false });
                  })
                }
              >
                Not rating this one
              </button>
            )}
          </div>
          {film.state === "watched_unrated" && film.rate_later && (
            <p className="muted">Waiting in your rate-later queue.</p>
          )}
          {film.state === "rated" && (
            <section className="rating-panel" aria-labelledby="rating-heading">
              <h2 id="rating-heading">Your rating</h2>
              <p className="film-page-band">
                <Band band={film.rating} />
                {film.anchor && <AnchorBadge band={film.rating} />}
                {/* The same ambient marker the film wears on Rated, and the reason the
                    offer below reads "settle it now" (surfacing.md). */}
                {film.provisional && <ProvisionalMark />}
              </p>
              {film.drift !== null && <Drift film={film} flag={film.drift} onChanged={onChange} />}
              {film.rewatch !== null && <Rewatch film={film} onChanged={onChange} />}
              {/* One row: logging another watch and asking to place it again are the two
                  things the owner does to a film they already have an opinion about. Both
                  stand down at once while a rewatch and a flag are open, and an empty row
                  is not a row. */}
              {(film.rewatch === null || film.drift === null) && (
                <div className="actions">
                  <Watched film={film} onChanged={onChange} />
                  {film.drift === null && <RePlace film={film} />}
                </div>
              )}
              {film.drift === null && film.anchor && <AnchorWarning band={film.rating} />}
              <Designate film={film} onChanged={onChange} />
              <p className="muted">
                <Link to="/rated">See where it sits in your ordering</Link>
              </p>
            </section>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
      </article>
    </>
  );
}

/**
 * The owner asking outright to place this film again: the fourth door (rating-system.md).
 *
 * One control with two wordings, because the two cases are genuinely different acts. A
 * provisional film holds a placeholder position and unfinished business, so the offer is
 * to finish it; a settled one holds a position the owner's own answers produced, so the
 * offer is to question it. Either way the comparisons decide and this only opens the flow.
 *
 * It stands down while a drift flag is open. That panel asks this exact question already,
 * with the evidence attached, and two buttons into one flow - one of them without the
 * evidence - would read as two different offers.
 */
function RePlace({ film }: { film: FilmDetail }) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();

  return (
    <>
      <button
        type="button"
        className="button secondary"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            await api.askToRePlace(film.tmdb_id);
            await navigate(placePath(film.tmdb_id));
          })
        }
      >
        {film.provisional ? "Settle it now" : "Re-place it"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

/**
 * What re-placing an anchor costs, said before the flow starts rather than after.
 *
 * Shared by the drift panel and the plain offer, because settling never bypasses it: a
 * canonical 4.0 that answers its way into the 3.5s is a contradiction in terms, and the
 * owner is entitled to know that before they answer rather than when the badge vanishes.
 */
function AnchorWarning({ band }: { band: number | null }) {
  return (
    <p className="muted">
      This is a canonical {band?.toFixed(1)}. Re-placing it somewhere else retires that,
      and the comparisons decide where it lands.
    </p>
  );
}

/**
 * An open drift flag, and the three ways out of it.
 *
 * The judgments are shown as the owner made them, because the whole question is "did
 * you mean these?" - and re-place, keep, and not-now are the only answers offered.
 * Dragging the film to a slot is deliberately absent: every move goes through
 * comparisons, so changing your mind means answering questions, not choosing a rank.
 *
 * Nothing here is urgent. The film is already benched as an opponent, so leaving this
 * open costs the ordering nothing, and "not now" is a real answer rather than a delay.
 */
function Drift({
  film,
  flag,
  onChanged,
}: {
  film: FilmDetail;
  flag: DriftFlag;
  onChanged: (film: FilmDetail) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();
  const [keeping, setKeeping] = useState(false);
  const [blamed, setBlamed] = useState<number[]>([]);

  // One follow-up per implicated opponent, not per judgment: the owner is deciding about
  // a film, and two answers about the same one is still only one film to blame.
  const implicated = [...new Map(flag.judgments.map((j) => [j.opponent.tmdb_id, j.opponent]))];
  const opponents: KeepOpponent[] = implicated.map(([tmdbId]) => ({
    opponent_tmdb_id: tmdbId,
    resolution: blamed.includes(tmdbId) ? "re_point" : "noise",
  }));

  return (
    <section className="drift-panel" aria-labelledby="drift-heading">
      <h3 id="drift-heading">This may have drifted</h3>
      <p className="muted">
        {flag.judgments.length === 1
          ? "A later answer disagrees with where this sits:"
          : "Later answers disagree with where this sits:"}
      </p>
      <ul className="drift-judgments">
        {said(flag).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>

      {keeping ? (
        <div className="drift-keep">
          <p className="muted">
            Keeping it here, and treating those as slips. Unless one of the other films is
            the misplaced one - then say so, and the question moves to it.
          </p>
          <ul className="drift-judgments">
            {implicated.map(([tmdbId, opponent]) => (
              <li key={`blame-${tmdbId}`}>
                <label className="field field-check">
                  <input
                    type="checkbox"
                    checked={blamed.includes(tmdbId)}
                    onChange={(event) =>
                      setBlamed((was) =>
                        event.target.checked ? [...was, tmdbId] : was.filter((id) => id !== tmdbId),
                      )
                    }
                  />
                  <span>{opponent.title} is the misplaced one</span>
                </label>
              </li>
            ))}
          </ul>
          <div className="actions">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.keepPosition(film.tmdb_id, opponents);
                  onChanged(await api.film(film.tmdb_id));
                })
              }
            >
              Keep it here
            </button>
            <button
              type="button"
              className="link-button"
              disabled={busy}
              onClick={() => setKeeping(false)}
            >
              Back
            </button>
          </div>
        </div>
      ) : (
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.rePlaceDrift(film.tmdb_id);
                navigate(placePath(film.tmdb_id));
              })
            }
          >
            {flag.re_placing ? "Carry on re-placing it" : "My opinion changed"}
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() => setKeeping(true)}
          >
            Those were noise
          </button>
        </div>
      )}

      {flag.anchor_warning && <AnchorWarning band={film.rating} />}
      {/* No "not now" button: leaving the page *is* not now, and the flag will still be
          here. Nothing is blocked while it waits. */}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

/**
 * What the owner actually said, one line per thing they said, however often they said it.
 *
 * Two answers about the same pair are two separate judgments to the engine and the same
 * sentence to a reader, so repeating the line verbatim reads as a rendering fault rather
 * than as evidence. Counting them says the true and more useful thing: you have told me
 * this twice, which is exactly why you are being asked.
 */
function said(flag: DriftFlag): string[] {
  const counts = new Map<string, number>();
  for (const judgment of flag.judgments) {
    const line = judgment.tied
      ? `You called it equal to ${judgment.opponent.title}`
      : judgment.opponent_won
        ? `You put ${judgment.opponent.title} above it`
        : `You put it above ${judgment.opponent.title}`;
    counts.set(line, (counts.get(line) ?? 0) + 1);
  }
  return [...counts].map(([line, times]) =>
    times === 1 ? `${line}.` : `${line} - ${times === 2 ? "twice" : `${times} times`}.`,
  );
}

/**
 * The still-feel-the-same question, offered once at the rewatch and never chased.
 *
 * Confirming is a signal about the position and moves nothing. Changing your mind opens
 * the same re-placement flow everything else does, seeded from where the film sits now,
 * and the comparisons decide from there.
 */
function Rewatch({
  film,
  onChanged,
}: {
  film: FilmDetail;
  onChanged: (film: FilmDetail) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();

  async function answer(choice: "confirmed" | "changed" | "skip") {
    await run(async () => {
      await api.answerRewatch(film.tmdb_id, choice);
      if (choice === "changed") navigate(placePath(film.tmdb_id));
      else onChanged(await api.film(film.tmdb_id));
    });
  }

  return (
    <section className="rewatch-panel" aria-labelledby="rewatch-heading">
      <h3 id="rewatch-heading">Still feel the same?</h3>
      <div className="actions">
        <button type="button" className="button" disabled={busy} onClick={() => void answer("confirmed")}>
          Yes, it holds up
        </button>
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => void answer("changed")}
        >
          I feel differently now
        </button>
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() => void answer("skip")}
        >
          Not sure
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

/** Logging another watch of a film already rated, which is the rewatch flow. */
function Watched({ film, onChanged }: { film: FilmDetail; onChanged: (film: FilmDetail) => void }) {
  const { busy, error, run } = useAsyncAction();

  if (film.rewatch !== null) return null;
  return (
    <>
      <button
        type="button"
        className="button secondary"
        disabled={busy}
        onClick={() => void run(async () => onChanged(await api.logRewatch(film.tmdb_id)))}
      >
        I watched this again
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

/**
 * Designating this film as a band's canonical exemplar, from its own page.
 *
 * Any band can be chosen, not just the one the film currently derives into: on a fresh
 * account nothing derives into anything yet, and designating is the one sanctioned way
 * to say what a band *is*. Choosing a band the film is not currently in does not move
 * it there - it starts a re-placement seeded by the intent, and the comparisons decide.
 */
function Designate({
  film,
  onChanged,
}: {
  film: FilmDetail;
  onChanged: (film: FilmDetail) => void;
}) {
  const navigate = useNavigate();
  const { busy, error, run } = useAsyncAction();
  const [band, setBand] = useState<number>(film.rating ?? 4);

  return (
    <div className="actions designate">
      <label className="field">
        <span>Make this my canonical…</span>
        <select
          value={band}
          disabled={busy}
          onChange={(event) => setBand(Number(event.target.value))}
        >
          {BANDS.map((value) => (
            <option key={value} value={value}>
              {value.toFixed(1)}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="button secondary"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            const result = await api.designate(band, film.tmdb_id);
            if (result.outcome === "re_placement") navigate(placePath(film.tmdb_id));
            else onChanged(await api.film(film.tmdb_id));
          })
        }
      >
        Designate
      </button>
      {film.anchor && (
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              if (film.rating !== null) await api.retireAnchor(film.rating);
              onChanged(await api.film(film.tmdb_id));
            })
          }
        >
          Retire this anchor
        </button>
      )}
      {band !== film.rating && (
        <p className="muted">
          {film.rating === null
            ? "Nothing is a known band yet, so this is what erects the first one."
            : `It is a ${film.rating.toFixed(1)} today, so this re-places it first and the comparisons decide.`}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function BackToSearch() {
  return (
    <p className="muted back-link">
      <Link to="/search">Back to search</Link>
    </p>
  );
}

function runtime(minutes: number | null): string {
  return minutes === null ? "Runtime unknown" : `${minutes} min`;
}
