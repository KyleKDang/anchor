import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api, messageOf, type FilmDetail } from "../api";
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
 * the watch, watched-unrated carries the seen marker and place-it-now, and rated points
 * at where the film sits. Pin and veto arrive with the ranked tier; the rated view's
 * band, judgment history, and re-place arrive with the tickets that create them.
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
        <div className="film-page-body">
          <h1>{film.title}</h1>
          <p className="film-row-meta">
            <span className="muted">
              {[releaseYear(film.year), runtime(film.runtime)].join(" · ")}
            </span>
            <StateFlag state={film.state} rating={film.rating} />
          </p>
          {film.genres.length > 0 && <p className="muted">{film.genres.join(", ")}</p>}
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

          <div className="film-actions">
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
            <p className="muted">
              <Link to="/rated">See where it sits in your ordering</Link>
            </p>
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
