import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import { api, messageOf, type Backlog, type BacklogFilm, type BacklogSort } from "../api";
import { Poster } from "../films/Poster";
import { filmPath, releaseYear } from "../films/tmdb";

const SORTS: { value: BacklogSort; label: string }[] = [
  { value: "added", label: "Recently added" },
  { value: "title", label: "Title" },
  { value: "year", label: "Year" },
];

const ALL = "";

/**
 * The Watchlist screen. Two tiers eventually, ranked over backlog; before taste-profile
 * readiness the screen is honestly just the backlog, which is all there is today.
 *
 * There is no sort by how good the engine thinks a film is, deliberately: ADR 0005 bars
 * anything rating-shaped on unwatched films.
 */
export function Watchlist() {
  const [sort, setSort] = useState<BacklogSort>("added");
  const [genre, setGenre] = useState(ALL);
  const [decade, setDecade] = useState(ALL);
  const [backlog, setBacklog] = useState<Backlog | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBacklog(
        await api.backlog({
          sort,
          genre: genre === ALL ? null : genre,
          decade: decade === ALL ? null : Number(decade),
        }),
      );
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [sort, genre, decade]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <h1>Watchlist</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {backlog !== null && (
        <section className="section" aria-labelledby="backlog-heading">
          <h2 id="backlog-heading">Backlog</h2>
          <div className="filters">
            <label className="field">
              <span>Sort by</span>
              <select value={sort} onChange={(event) => setSort(event.target.value as BacklogSort)}>
                {SORTS.map(({ value, label }) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Genre</span>
              <select value={genre} onChange={(event) => setGenre(event.target.value)}>
                <option value={ALL}>All genres</option>
                {backlog.genres.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Decade</span>
              <select value={decade} onChange={(event) => setDecade(event.target.value)}>
                <option value={ALL}>All decades</option>
                {backlog.decades.map((start) => (
                  <option key={start} value={String(start)}>
                    {start}s
                  </option>
                ))}
              </select>
            </label>
          </div>

          {backlog.films.length === 0 ? (
            <p className="muted">
              Nothing here yet. <Link to="/search">Search for a film</Link> to add one.
            </p>
          ) : (
            <ul className="film-list">
              {backlog.films.map((film) => (
                <BacklogRow key={film.tmdb_id} film={film} onWatched={() => void load()} />
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}

function BacklogRow({ film, onWatched }: { film: BacklogFilm; onWatched: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function markWatched() {
    setBusy(true);
    setError(null);
    try {
      await api.markWatched(film.tmdb_id);
      onWatched();
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <li className="film-row">
      <Link to={filmPath(film.tmdb_id)} tabIndex={-1} aria-hidden="true">
        <Poster title={film.title} path={film.poster_path} size="w154" />
      </Link>
      <div className="film-row-body">
        <h3 className="film-row-title">
          <Link to={filmPath(film.tmdb_id)}>{film.title}</Link>
        </h3>
        <p className="film-row-meta">
          <span className="muted">
            {[releaseYear(film.year), film.genres.join(", ")].filter(Boolean).join(" · ")}
          </span>
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      {/* Mark-watched is the verb a backlog row carries today; pin and veto belong to the
          ranked tier, which does not exist until the taste profile is ready. */}
      <button type="button" className="button secondary" onClick={markWatched} disabled={busy}>
        Mark watched
      </button>
    </li>
  );
}
