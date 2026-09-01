import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api, messageOf, type FilmDetail } from "../api";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { StateFlag } from "../films/StateFlag";
import { releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The film page, reached by tapping a film anywhere. It is not a destination of its own.
 *
 * The page shifts with the film's state; the states that exist today are untracked and
 * in-backlog, plus the seen marker a watched-unrated film carries. Pin, veto, and the
 * rated view arrive with the ranked tier and the ordering.
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
            {film.state !== "watched_unrated" && film.state !== "rated" && (
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => void run(async () => onChange(await api.markWatched(film.tmdb_id)))}
              >
                I watched this
              </button>
            )}
          </div>
          {film.state === "watched_unrated" && (
            <p className="muted">Waiting in your rate-later queue.</p>
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
