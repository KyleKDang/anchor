import { useState, type FormEvent } from "react";

import { api, messageOf, type Browse, type SearchResult } from "../../api";
import { Plot } from "../../films/Plot";
import { Poster } from "../../films/Poster";
import { StateFlag } from "../../films/StateFlag";
import { releaseYear } from "../../films/tmdb";

/**
 * Search for a film and do one thing with it: the warmup's only way of naming a film.
 *
 * Search leads and the grid is a fallback the owner has to ask for, which is the whole
 * point of the arrangement. A popularity grid put first would have the owner designating
 * the films everybody has seen rather than the films they themselves know cold, and the
 * anchors would end up describing the box office instead of their taste
 * (onboarding-and-import.md).
 *
 * Rows are not links: mid-warmup a film page would be a way out of a flow that is two
 * clicks long, and the row's own button is the only verb the step needs.
 */
export function FilmPicker({
  label,
  action,
  onPick,
  browse = false,
  disabled = false,
  pickable = () => true,
}: {
  label: string;
  /** The verb on each row, e.g. "This is my 5.0". */
  action: string;
  onPick: (film: SearchResult) => Promise<void>;
  /** Offer the popular and top-rated grids as an explicit "need inspiration?" fallback. */
  browse?: boolean;
  disabled?: boolean;
  /** Rows this step cannot act on - already backlogged, already rated - go quiet. */
  pickable?: (film: SearchResult) => boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [grid, setGrid] = useState<Browse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(run: () => Promise<{ results: SearchResult[] }>) {
    setBusy(true);
    setError(null);
    try {
      setResults((await run()).results);
    } catch (caught) {
      setError(messageOf(caught));
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleSearch(event: FormEvent) {
    event.preventDefault();
    const term = query.trim();
    if (!term) return;
    setGrid(null);
    await load(() => api.searchFilms(term));
  }

  async function showGrid(kind: Browse) {
    setGrid(kind);
    await load(() => api.browseFilms(kind));
  }

  return (
    <div className="picker">
      <form className="search-form" onSubmit={(event) => void handleSearch(event)} role="search">
        <label className="field">
          <span>{label}</span>
          <input
            type="search"
            name="query"
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="submit" className="button" disabled={busy || disabled || !query.trim()}>
          Search
        </button>
      </form>

      {browse && (
        <p className="picker-fallback muted">
          Can't think of one?{" "}
          <button
            type="button"
            className="link-button"
            aria-pressed={grid === "popular"}
            disabled={busy || disabled}
            onClick={() => void showGrid("popular")}
          >
            Browse popular
          </button>{" "}
          or{" "}
          <button
            type="button"
            className="link-button"
            aria-pressed={grid === "top_rated"}
            disabled={busy || disabled}
            onClick={() => void showGrid("top_rated")}
          >
            browse top rated
          </button>
          .
        </p>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {results !== null && results.length === 0 && (
        <p className="muted">No films match that.</p>
      )}
      {results !== null && results.length > 0 && (
        <ul className="film-list picker-results">
          {results.map((film) => (
            <PickerRow
              key={film.tmdb_id}
              film={film}
              action={action}
              disabled={disabled || !pickable(film)}
              onPick={onPick}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function PickerRow({
  film,
  action,
  disabled,
  onPick,
}: {
  film: SearchResult;
  action: string;
  disabled: boolean;
  onPick: (film: SearchResult) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  return (
    <li className="film-row">
      <Poster title={film.title} path={film.poster_path} size="w92" />
      <div className="film-row-body">
        <h3 className="film-row-title">{film.title}</h3>
        <p className="film-row-meta">
          <span className="muted">{releaseYear(film.year)}</span>
          <StateFlag state={film.state} rating={film.rating} />
        </p>
        <Plot overview={film.overview} />
      </div>
      <div className="film-row-actions">
        <button
          type="button"
          className="button secondary"
          disabled={disabled || busy}
          onClick={() => {
            setBusy(true);
            void onPick(film).finally(() => setBusy(false));
          }}
        >
          {action}
        </button>
      </div>
    </li>
  );
}
