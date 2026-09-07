import { useCallback, useEffect, useMemo, useState } from "react";
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
import { useAuth } from "../auth";
import { AnchorBadge, AnchorNudge, Band } from "../films/Band";
import { Poster } from "../films/Poster";
import { filmPath, placePath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";
import { EditableWall } from "./rated/EditableWall";
import { editableBands } from "./rated/moves";

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
 *
 * One toggle turns the wall into the editor (screens-and-flows.md, "Edit mode"). It is
 * there only on the position sort, since a flat list has no rank to drag to, and never
 * on the demo account, whose wall is content rather than a control. Edit mode lives in
 * the URL: "Adjust on the wall" is then a plain link, and the back button leaves it.
 */
export function Rated() {
  const { account } = useAuth();
  const [filters, setFilters] = useState<RatedFilters>({ sort: "position" });
  const [rated, setRated] = useState<RatedScreen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const highlighted = Number(params.get("film")) || null;
  const editable = (filters.sort ?? "position") === "position" && account?.demo === false;
  const editing = editable && params.has("edit");
  const bands = useMemo(
    () => editableBands(filters.bandMax ?? null, filters.bandMin ?? null),
    [filters.bandMax, filters.bandMin],
  );

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
  const loaded = rated !== null;
  useEffect(() => {
    // Once the wall is on the page, and not again on every re-read behind a drop.
    if (!loaded || highlighted === null) return;
    document.getElementById(`film-${highlighted}`)?.scrollIntoView({ block: "center" });
  }, [loaded, highlighted]);

  // The ring goes when the film is moved or the owner leaves; both live in the URL.
  const unring = useCallback(
    () => setParams((current) => without(current, "film"), { replace: true }),
    [setParams],
  );
  const toggleEditing = useCallback(
    () =>
      setParams(
        (current) => (current.has("edit") ? without(current, "edit", "film") : withEdit(current)),
        { replace: true },
      ),
    [setParams],
  );

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
          <Controls
            rated={rated}
            filters={filters}
            onChange={setFilters}
            editable={editable}
            editing={editing}
            onToggleEditing={toggleEditing}
          />

          <section className="section" aria-labelledby="ordering-heading">
            <h2 id="ordering-heading">Your ordering</h2>
            {editing && rated.anchor_nudge && (
              <AnchorNudge action="Tap Anchor under any poster here to mark your first." />
            )}
            {editing && rated.rows !== null ? (
              <EditableWall
                rated={rated}
                bands={bands}
                highlighted={highlighted}
                onMoved={unring}
                onSettled={load}
              />
            ) : isEmpty(rated) ? (
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

/**
 * The sort, the filters, the edit toggle, and the jump-to-band strip that only the wall
 * can offer. Filters stay usable in edit mode: filtering a big band down to the films the
 * owner is thinking about is the intended way to work a long row.
 */
function Controls({
  rated,
  filters,
  onChange,
  editable,
  editing,
  onToggleEditing,
}: {
  rated: RatedScreen;
  filters: RatedFilters;
  onChange: (filters: RatedFilters) => void;
  editable: boolean;
  editing: boolean;
  onToggleEditing: () => void;
}) {
  const set = (patch: RatedFilters) => onChange({ ...filters, ...patch });

  return (
    <div className="rated-controls" data-editing={editing ? "true" : undefined}>
      {editable && (
        <div className="edit-bar">
          <button
            type="button"
            className={editing ? "button" : "button secondary"}
            aria-pressed={editing}
            onClick={onToggleEditing}
          >
            {editing ? "Done editing" : "Edit the wall"}
          </button>
          {editing && (
            <p className="muted edit-hint">
              Drag a poster to move it, within its band or into another. Every move saves at
              once.{" "}
              <span className="edit-hint-keys">
                With a poster selected, ← and → move it one rank, Shift for the ends, ↑ and ↓
                across bands; Esc drops a drag where it started.
              </span>
            </p>
          )}
        </div>
      )}
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

function without(params: URLSearchParams, ...keys: string[]): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of keys) next.delete(key);
  return next;
}

function withEdit(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  next.set("edit", "1");
  return next;
}

function isEmpty(rated: RatedScreen): boolean {
  return (rated.rows?.length ?? rated.films?.length ?? 0) === 0;
}

function hasFilters(filters: RatedFilters): boolean {
  return Boolean(
    filters.bandMin || filters.bandMax || filters.genre || filters.decade || filters.anchorsOnly,
  );
}
