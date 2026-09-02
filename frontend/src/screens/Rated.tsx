import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import { api, messageOf, type FilmCard, type Rated as RatedOrdering } from "../api";
import { Poster } from "../films/Poster";
import { filmPath, placePath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The Rated screen: the owner's ordering, best to worst, and the rate-later queue below.
 *
 * The ordering is shown position-only. Band grouping with half-star headers, anchors, and
 * the needs-attention strip all need dividers to exist, and with none pinned yet, grouping
 * by band would be grouping by nothing - so the screen says what it honestly knows.
 */
export function Rated() {
  const [rated, setRated] = useState<RatedOrdering | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRated(await api.rated());
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <h1>Rated</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {rated !== null && (
        <>
          <section className="section" aria-labelledby="ordering-heading">
            <h2 id="ordering-heading">Your ordering</h2>
            {rated.ordering.length === 0 ? (
              <p className="muted">
                Nothing placed yet. Mark a film watched and rate it now to start your ordering.
              </p>
            ) : (
              <ol className="ordering">
                {rated.ordering.map((slot) => (
                  <li key={slot.position} className="ordering-slot">
                    <span className="ordering-rank">{slot.position}</span>
                    <div className="ordering-films">
                      {slot.films.map((film) => (
                        <OrderedFilm key={film.tmdb_id} film={film} />
                      ))}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="section" aria-labelledby="rate-later-heading">
            <h2 id="rate-later-heading">Rate later</h2>
            {rated.rate_later.length === 0 ? (
              <p className="muted">Nothing waiting to be rated.</p>
            ) : (
              <ul className="film-list">
                {rated.rate_later.map((film) => (
                  <QueuedFilm key={film.tmdb_id} film={film} onLeft={() => void load()} />
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </>
  );
}

/** One film in a slot. Tie-group members sit together under one rank, as one slot. */
function OrderedFilm({ film }: { film: FilmCard }) {
  return (
    <p className="ordering-film">
      <Link to={filmPath(film.tmdb_id)}>{film.title}</Link>{" "}
      <span className="muted">{releaseYear(film.year)}</span>
    </p>
  );
}

function QueuedFilm({ film, onLeft }: { film: FilmCard; onLeft: () => void }) {
  const { busy, error, run } = useAsyncAction();

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
          <span className="muted">{releaseYear(film.year)}</span>
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="film-row-actions">
        <Link className="button" to={placePath(film.tmdb_id)}>
          Place it now
        </Link>
        {/* Leaving the queue says "I am not rating this one"; it stays watched. */}
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.leaveRateLater(film.tmdb_id);
              onLeft();
            })
          }
        >
          Not rating this one
        </button>
      </div>
    </li>
  );
}
