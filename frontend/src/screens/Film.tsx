import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, messageOf, type FilmDetail, type Judgment } from "../api";
import { AnchorBadge, Band } from "../films/Band";
import { MarkWatched } from "../films/MarkWatched";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { StateFlag } from "../films/StateFlag";
import { filmPath, placePath, questionsPath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The film page, reached by tapping a film anywhere. It is not a destination of its own.
 *
 * The page shifts with the film's state: untracked and in-backlog offer the backlog and
 * the watch, watched-unrated carries the seen marker and rate-it-now, and rated shows its
 * band, its rank inside that band with the films either side of it, the anchor toggle,
 * the rewatch, and its judgment history. Pin and veto arrive with the ranked tier.
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
                Rate it now
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
              </p>
              <Standing film={film} />
              {film.rewatch !== null && <Rewatch film={film} onChanged={onChange} />}
              {/* One row: logging another watch and rating it again are the two things the
                  owner does to a film they already have an opinion about. */}
              <div className="actions">
                <Watched film={film} onChanged={onChange} />
                <Link className="button secondary" to={placePath(film.tmdb_id)}>
                  Re-rate it
                </Link>
                <AnchorToggle film={film} onChanged={onChange} />
              </div>
              {/* Two ways on from here, one line each: the wall, and - pull-only, and the
                  one place it is offered (surfacing.md) - a stream of questions about this
                  film, open whatever the frequency setting says. */}
              <ul className="muted film-page-links">
                <li>
                  <Link to={`/rated?film=${film.tmdb_id}`}>See where it sits on the wall</Link>
                </li>
                <li>
                  <Link to={questionsPath(film.tmdb_id)}>Answer questions about this film</Link>
                </li>
              </ul>
              <Judgments judgments={film.judgments} />
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
      // Changing your mind opens the picker with the current band marked; the pick
      // decides the rating the same way every other pick does (rating-system.md).
      if (choice === "changed") await navigate(placePath(film.tmdb_id));
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
 * Where the film sits inside its band, and the films either side of it.
 *
 * Band-local, because the rank is: "third of your 4.0s" is a statement about the 4.0s,
 * and the film at the end of a row genuinely has no neighbour that way.
 */
function Standing({ film }: { film: FilmDetail }) {
  if (film.rank === null || film.band_size === null) return null;
  const { above = null, below = null } = film.neighbours ?? {};

  return (
    <div className="standing">
      <p className="muted">
        Number {film.rank} of {film.band_size} in that band.
      </p>
      {(above || below) && (
        <ul className="standing-neighbours">
          {above && (
            <li>
              <span className="muted">Above it</span>{" "}
              <Link to={filmPath(above.tmdb_id)}>{above.title}</Link>
            </li>
          )}
          {below && (
            <li>
              <span className="muted">Below it</span>{" "}
              <Link to={filmPath(below.tmdb_id)}>{below.title}</Link>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

/**
 * The anchor toggle: one control, marking and retiring alike (screens-and-flows.md).
 *
 * Marking says the owner is certain of this film's rating, which is what puts it in the
 * band picker's pool for that band. It changes nothing else - not the rating, not the
 * rank - so there is nothing to warn about before tapping it.
 */
function AnchorToggle({
  film,
  onChanged,
}: {
  film: FilmDetail;
  onChanged: (film: FilmDetail) => void;
}) {
  const { busy, error, run } = useAsyncAction();

  return (
    <>
      <button
        type="button"
        className="button secondary"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            if (film.anchor) await api.retireAnchor(film.tmdb_id);
            else await api.markAnchor(film.tmdb_id);
            onChanged(await api.film(film.tmdb_id));
          })
        }
      >
        {film.anchor ? "Retire this anchor" : "Mark as an anchor"}
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
 * The judgment history: this film's comparison-log entries, newest first.
 *
 * Shown as the owner made them and never flagged. An entry the ordering has since moved
 * past is not marked or superseded - the reader compares it with the band and rank above,
 * and the ordering wins (ADR 0013).
 */
function Judgments({ judgments }: { judgments: Judgment[] }) {
  if (judgments.length === 0) return null;

  return (
    <section className="judgments" aria-labelledby="judgments-heading">
      <h3 id="judgments-heading">What you have said about it</h3>
      <ul className="judgment-list">
        {judgments.map((judgment, index) => (
          <li key={index}>
            {said(judgment)}
            <span className="muted"> · {when(judgment.created_at)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One judgment in the owner's own terms rather than the log's. */
function said(judgment: Judgment): string {
  if (judgment.kind === "band_pick") {
    return `You rated it ${judgment.band?.toFixed(1) ?? ""}`;
  }
  if (judgment.kind === "criteria") {
    const quality = judgment.quality?.toLowerCase() ?? "it";
    if (judgment.verdict === "tied") return `You called them level on ${quality}`;
    if (judgment.verdict === "skip") return `You were asked about ${quality}`;
    const better = judgment.verdict === "a" ? "it" : (judgment.other?.title ?? "the other");
    return `You said ${better} had the better ${quality}`;
  }
  const other = judgment.other?.title ?? "another film";
  if (judgment.verdict === "tied") return `You called it about the same as ${other}`;
  if (judgment.verdict === "skip") return `You skipped a question about ${other}`;
  return judgment.verdict === "a" ? `You put it above ${other}` : `You put ${other} above it`;
}

function when(timestamp: string): string {
  return new Date(timestamp).toLocaleDateString();
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
