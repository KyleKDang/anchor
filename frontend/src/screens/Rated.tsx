import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import {
  BANDS,
  api,
  messageOf,
  type BandRow,
  type FilmCard,
  type Rated as RatedScreen,
  type RatedFilm,
  type RatedFilters,
  type RatedSort,
} from "../api";
import { AnchorBadge, AnchorNudge, Band } from "../films/Band";
import { Poster } from "../films/Poster";
import { filmPath, placePath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The Rated screen: the ordering as ten band rows, and the rate-later queue below it.
 *
 * The wall is the ordering read back exactly as it is stored - best band first, the
 * half-star value as each row's header with the count of that band's anchors, and the
 * rank stamped on every poster. There is no derivation here and nothing that can be out
 * of step with what the owner sees (ADR 0013).
 *
 * Every other sort cuts across the bands, so it drops the rows and shows a flat list: a
 * band header over a sequence that is not in band order would be a heading over nothing.
 *
 * The screen is a pull surface through and through (ADR 0011). No film is marked as
 * wanting attention and no move is ever suggested.
 */
export function Rated() {
  const [filters, setFilters] = useState<RatedFilters>({ sort: "position" });
  const [rated, setRated] = useState<RatedScreen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [params] = useSearchParams();
  const highlighted = Number(params.get("film")) || null;

  const load = useCallback(async () => {
    try {
      setRated(await api.rated(filters));
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  // "Adjust on the wall" lands here with the film named, and the poster it points at does
  // not exist until this screen's fetch resolves - so the browser has nothing to scroll to
  // at navigation time. Scroll once the wall is actually on the page.
  useEffect(() => {
    if (rated === null || highlighted === null) return;
    document.getElementById(`film-${highlighted}`)?.scrollIntoView({ block: "center" });
  }, [rated, highlighted]);

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
          {rated.anchor_nudge && <AnchorNudge film={firstFilm(rated)} />}
          <Controls rated={rated} filters={filters} onChange={setFilters} />

          <section className="section" aria-labelledby="ordering-heading">
            <h2 id="ordering-heading">Your ordering</h2>
            {isEmpty(rated) ? (
              <div className="empty">
                <p className="muted">
                  {hasFilters(filters)
                    ? "No rated films match these filters."
                    : "Nothing rated yet. Mark a film watched and rate it now to start your wall."}
                </p>
              </div>
            ) : rated.rows !== null ? (
              rated.rows.map((row) => (
                <BandSection key={row.band} row={row} highlighted={highlighted} />
              ))
            ) : (
              <ol className="ordering">
                {rated.films?.map((film) => (
                  <WallCell
                    key={film.tmdb_id}
                    film={film}
                    showBand
                    highlighted={film.tmdb_id === highlighted}
                  />
                ))}
              </ol>
            )}
          </section>

          <section className="section" aria-labelledby="rate-later-heading">
            <h2 id="rate-later-heading">Rate later</h2>
            {rated.rate_later.length === 0 ? (
              <div className="empty">
                <p className="muted">Nothing waiting to be rated.</p>
              </div>
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

const SORTS: { value: RatedSort; label: string }[] = [
  { value: "position", label: "Your ordering" },
  { value: "rated", label: "Recently rated" },
  { value: "watched", label: "Recently watched" },
  { value: "title", label: "Title" },
  { value: "year", label: "Release year" },
];

/** The sort, the filters, and the jump-to-band strip that only the wall can offer. */
function Controls({
  rated,
  filters,
  onChange,
}: {
  rated: RatedScreen;
  filters: RatedFilters;
  onChange: (filters: RatedFilters) => void;
}) {
  const set = (patch: RatedFilters) => onChange({ ...filters, ...patch });

  return (
    <div className="rated-controls">
      <div className="filters">
        <label className="field">
          <span>Sort</span>
          <select
            value={filters.sort ?? "position"}
            onChange={(event) => set({ sort: event.target.value as RatedSort })}
          >
            {SORTS.map((sort) => (
              <option key={sort.value} value={sort.value}>
                {sort.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>From</span>
          <BandSelect
            value={filters.bandMax ?? null}
            onChange={(band) => set({ bandMax: band })}
            any="Best"
          />
        </label>
        <label className="field">
          <span>To</span>
          <BandSelect
            value={filters.bandMin ?? null}
            onChange={(band) => set({ bandMin: band })}
            any="Worst"
          />
        </label>
        <label className="field">
          <span>Genre</span>
          <select
            value={filters.genre ?? ""}
            onChange={(event) => set({ genre: event.target.value || null })}
          >
            <option value="">Any</option>
            {rated.genres.map((genre) => (
              <option key={genre} value={genre}>
                {genre}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Decade</span>
          <select
            value={filters.decade ?? ""}
            onChange={(event) => set({ decade: Number(event.target.value) || null })}
          >
            <option value="">Any</option>
            {rated.decades.map((decade) => (
              <option key={decade} value={decade}>
                {decade}s
              </option>
            ))}
          </select>
        </label>
        {/* A checkbox rather than a select: it has one useful setting, and the other
            one is simply the screen as it always was. */}
        <label className="field field-check">
          <input
            type="checkbox"
            checked={filters.anchorsOnly ?? false}
            onChange={(event) => set({ anchorsOnly: event.target.checked })}
          />
          <span>Anchors only</span>
        </label>
      </div>
      {rated.rows !== null && rated.bands.length > 0 && (
        <nav className="jump-to-band" aria-label="Jump to band">
          <span className="muted">Jump to</span>
          {rated.bands.map((band) => (
            <a key={band} href={`#${bandId(band)}`}>
              {band.toFixed(1)}
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}

/** Bands run best to worst, so the range reads top-down the way the wall does. */
function BandSelect({
  value,
  onChange,
  any,
}: {
  value: number | null;
  onChange: (band: number | null) => void;
  any: string;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
    >
      <option value="">{any}</option>
      {BANDS.map((band) => (
        <option key={band} value={band}>
          {band.toFixed(1)}
        </option>
      ))}
    </select>
  );
}

function bandId(band: number): string {
  return `band-${String(band).replace(".", "-")}`;
}

/**
 * One band row, under its half-star header.
 *
 * The header carries the count of the band's anchors, which is a fact about the band
 * rather than about the current view: a filter thins the row without changing what the
 * band holds (screens-and-flows.md).
 */
function BandSection({ row, highlighted }: { row: BandRow; highlighted: number | null }) {
  return (
    <section className="band-group" id={bandId(row.band)} aria-label={`${row.band.toFixed(1)} stars`}>
      <header className="band-header">
        <h3>
          <Band band={row.band} />
        </h3>
        {row.anchors > 0 && (
          <span className="muted">
            {row.anchors} {row.anchors === 1 ? "anchor" : "anchors"}
          </span>
        )}
      </header>
      <ol className="ordering">
        {row.films.map((film) => (
          <WallCell key={film.tmdb_id} film={film} highlighted={film.tmdb_id === highlighted} />
        ))}
      </ol>
    </section>
  );
}

/**
 * One cell of the wall: a film under its rank inside its band.
 *
 * The rank is the film's place in its band, not its place in the current view - so a
 * filtered row shows the ranks the films actually hold rather than renumbering them,
 * which would be showing the owner a position no film occupies.
 */
function WallCell({
  film,
  showBand = false,
  highlighted = false,
}: {
  film: RatedFilm;
  showBand?: boolean;
  highlighted?: boolean;
}) {
  return (
    <li
      className="ordering-slot"
      id={`film-${film.tmdb_id}`}
      data-highlighted={highlighted ? "true" : undefined}
    >
      <span className="ordering-rank">{film.rank}</span>
      <OrderedFilm film={film} showBand={showBand} />
    </li>
  );
}

/**
 * One film on the wall: its poster, and the title and marks under it.
 *
 * The poster is the point of the wall - a poster is recognised faster than a title, and
 * at three hundred films the wall is shorter than the list of rows it replaces.
 *
 * The band shows under a film only where the list is flat: inside the wall's band rows
 * the value is already in the header above, and repeating it on every poster would turn
 * the header into decoration.
 */
function OrderedFilm({ film, showBand = false }: { film: RatedFilm; showBand?: boolean }) {
  return (
    <div className="ordering-film">
      <Link className="poster-link" to={filmPath(film.tmdb_id)} tabIndex={-1} aria-hidden="true">
        <Poster title={film.title} path={film.poster_path} size="w342" />
      </Link>
      <div className="ordering-film-body">
        <Link className="film-title" to={filmPath(film.tmdb_id)}>
          {film.title}
        </Link>
        <span className="film-year muted">{releaseYear(film.year)}</span>
        {showBand && <Band band={film.band} />}
        {film.anchor && <AnchorBadge band={film.band} />}
      </div>
    </div>
  );
}

function QueuedFilm({ film, onLeft }: { film: FilmCard; onLeft: () => void }) {
  const { busy, error, run } = useAsyncAction();

  return (
    <li className="film-row">
      <Link className="poster-link" to={filmPath(film.tmdb_id)} tabIndex={-1} aria-hidden="true">
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
          Rate it now
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

function isEmpty(rated: RatedScreen): boolean {
  return (rated.rows?.length ?? rated.films?.length ?? 0) === 0;
}

function hasFilters(filters: RatedFilters): boolean {
  return Boolean(
    filters.bandMin || filters.bandMax || filters.genre || filters.decade || filters.anchorsOnly,
  );
}

/** The nudge points at the owner's best film, which is the easiest one to have an opinion about. */
function firstFilm(rated: RatedScreen): RatedFilm | undefined {
  return rated.rows?.[0]?.films[0] ?? rated.films?.[0];
}
