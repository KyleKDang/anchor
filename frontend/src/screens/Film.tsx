import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { BANDS, api, messageOf, type FilmDetail } from "../api";
import { AnchorBadge, Band } from "../films/Band";
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
 * its band, whether it anchors that band, and the designation control. Pin and veto
 * arrive with the ranked tier; judgment history and drift resolution with theirs.
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
              </p>
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
