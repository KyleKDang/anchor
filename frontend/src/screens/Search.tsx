import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { api, messageOf, type LifecycleState, type SearchResult } from "../api";
import { MarkWatched } from "../films/MarkWatched";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { StateFlag } from "../films/StateFlag";
import { filmPath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/** One dedicated screen searching TMDB, with the owner's own films flagged inline. */
export function Search() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSearch(event: FormEvent) {
    event.preventDefault();
    const term = query.trim();
    if (!term) return;
    setBusy(true);
    setError(null);
    try {
      setResults((await api.searchFilms(term)).results);
    } catch (caught) {
      setError(messageOf(caught));
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  /** Keep the flag on the row the owner just acted on, without re-running the search. */
  function reflag(tmdbId: number, state: LifecycleState) {
    setResults(
      (current) =>
        current?.map((result) => (result.tmdb_id === tmdbId ? { ...result, state } : result)) ??
        null,
    );
  }

  return (
    <>
      <h1>Search</h1>
      <form className="search-form" onSubmit={handleSearch} role="search">
        <label className="field">
          <span>Find a film</span>
          <input
            type="search"
            name="query"
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="submit" className="button" disabled={busy || !query.trim()}>
          Search
        </button>
      </form>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {results === null && !error && <p className="muted">Search TMDB for a film by title.</p>}
      {results !== null && results.length === 0 && <p className="muted">No films match that.</p>}
      {results !== null && results.length > 0 && (
        <ul className="film-list">
          {results.map((result) => (
            <ResultRow key={result.tmdb_id} result={result} onTracked={reflag} />
          ))}
        </ul>
      )}
    </>
  );
}

function ResultRow({
  result,
  onTracked,
}: {
  result: SearchResult;
  onTracked: (tmdbId: number, state: LifecycleState) => void;
}) {
  const { busy, error, run } = useAsyncAction();

  async function addToBacklog() {
    await run(async () => {
      await api.addToBacklog(result.tmdb_id);
      onTracked(result.tmdb_id, "backlog");
    });
  }

  return (
    <li className="film-row">
      <Link to={filmPath(result.tmdb_id)} tabIndex={-1} aria-hidden="true">
        <Poster title={result.title} path={result.poster_path} size="w154" />
      </Link>
      <div className="film-row-body">
        <h2 className="film-row-title">
          <Link to={filmPath(result.tmdb_id)}>{result.title}</Link>
        </h2>
        <p className="film-row-meta">
          <span className="muted">{releaseYear(result.year)}</span>
          <StateFlag state={result.state} rating={result.rating} />
        </p>
        <Plot overview={result.overview} />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      {/* Adding and logging a watch are the verbs search carries inline; everything
          else - pin, veto, re-place - is on the film page. */}
      {(result.state === null || result.state === "backlog") && (
        <div className="film-row-actions">
          {result.state === null && (
            <button
              type="button"
              className="button secondary"
              onClick={addToBacklog}
              disabled={busy}
            >
              Add to backlog
            </button>
          )}
          <MarkWatched
            tmdbId={result.tmdb_id}
            onLater={() => onTracked(result.tmdb_id, "watched_unrated")}
          />
        </div>
      )}
    </li>
  );
}
