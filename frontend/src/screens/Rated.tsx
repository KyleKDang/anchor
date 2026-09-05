import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import {
  BANDS,
  api,
  messageOf,
  type BandGroup,
  type FilmCard,
  type Rated as RatedScreen,
  type RatedFilm,
  type RatedFilters,
  type RatedSort,
} from "../api";
import { AnchorBadge, AnchorNudge, Band, ProvisionalMark } from "../films/Band";
import { Poster } from "../films/Poster";
import { filmPath, placePath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The Rated screen: the ordering grouped into bands, and the rate-later queue below it.
 *
 * The default view is the ordering itself, best to worst, with the half-star value as
 * each group's header and the band's anchor badged. A run the dividers cannot decide
 * groups under "Rating pending" rather than under a made-up number - the honest state a
 * fresh account lives in until the first anchors erect some structure.
 *
 * Every other sort cuts across the ordering, so it drops the grouping and shows a flat
 * list: a band header over a sequence that is not in band order would be a heading over
 * nothing.
 */
export function Rated() {
  const [filters, setFilters] = useState<RatedFilters>({ sort: "position" });
  const [rated, setRated] = useState<RatedScreen | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          <NeedsAttention films={rated.needs_attention} />
          <Controls rated={rated} filters={filters} onChange={setFilters} />

          <section className="section" aria-labelledby="ordering-heading">
            <h2 id="ordering-heading">Your ordering</h2>
            {isEmpty(rated) ? (
              <div className="empty">
                <p className="muted">
                  {hasFilters(filters)
                    ? "No rated films match these filters."
                    : "Nothing placed yet. Mark a film watched and rate it now to start your ordering."}
                </p>
              </div>
            ) : rated.groups !== null ? (
              rated.groups.map((group, index) => (
                <BandSection
                  key={`${group.band ?? "pending"}-${index}`}
                  group={group}
                  onChange={() => void load()}
                />
              ))
            ) : (
              <ol className="ordering">
                {rated.films?.map((film) => (
                  <WallCell key={film.tmdb_id} film={film} showBand />
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

/**
 * The needs-attention strip: the films whose position the owner's own later answers
 * have started to disagree with.
 *
 * This is the loudest drift ever gets (ADR 0011). It sits at the top of one screen,
 * says how many and which, and waits - no count in the nav, no dot, no notification
 * anywhere else in the app. It renders nothing at all when nothing is wrong, which is
 * most of the time, and that is the point: it is presence, not a permanent slot.
 */
function NeedsAttention({ films }: { films: FilmCard[] }) {
  if (films.length === 0) return null;

  return (
    <section className="needs-attention" aria-labelledby="needs-attention-heading">
      <h2 id="needs-attention-heading" className="needs-attention-heading">
        {films.length === 1
          ? "One film may have drifted"
          : `${films.length} films may have drifted`}
      </h2>
      <p className="muted">
        {films.length === 1
          ? "Later answers disagree with where it sits. Open it to re-place it or keep it where it is."
          : "Later answers disagree with where these sit. Open one to re-place it or keep it where it is."}
      </p>
      <ul className="needs-attention-films">
        {films.map((film) => (
          <li key={film.tmdb_id}>
            <Link className="chip" to={filmPath(film.tmdb_id)}>
              {film.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

const SORTS: { value: RatedSort; label: string }[] = [
  { value: "position", label: "Your ordering" },
  { value: "rated", label: "Recently rated" },
  { value: "watched", label: "Recently watched" },
  { value: "title", label: "Title" },
  { value: "year", label: "Release year" },
];

/** The sort, the filters, and the jump-to-band strip that only the ordering can offer. */
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
            checked={filters.flagged ?? false}
            onChange={(event) => set({ flagged: event.target.checked })}
          />
          <span>Needs attention</span>
        </label>
      </div>
      {rated.groups !== null && rated.bands.length > 0 && (
        <nav className="jump-to-band" aria-label="Jump to band">
          <span className="muted">Jump to</span>
          {rated.bands.map((band) => (
            <a key={band} href={`#band-${String(band).replace(".", "-")}`}>
              {band.toFixed(1)}
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}

/** Bands run best to worst, so the range reads top-down the way the ordering does. */
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

/** One band's run of the ordering, under its half-star header. */
function BandSection({ group, onChange }: { group: BandGroup; onChange: () => void }) {
  const id = group.band === null ? undefined : `band-${String(group.band).replace(".", "-")}`;
  const anchored = group.slots.flat().find((film) => film.anchor) ?? null;

  return (
    <section className="band-group" id={id} aria-label={header(group.band)}>
      <header className="band-header">
        <h3>
          <Band band={group.band} />
        </h3>
        {group.band !== null && (
          <AnchorPicker
            band={group.band}
            films={group.slots.flat()}
            anchored={anchored}
            onChange={onChange}
          />
        )}
      </header>
      <ol className="ordering">
        {group.slots.flatMap((slot) =>
          slot.map((film) => <WallCell key={film.tmdb_id} film={film} tie={slot.length > 1} />),
        )}
      </ol>
    </section>
  );
}

function header(band: number | null): string {
  return band === null ? "Rating pending" : `${band.toFixed(1)} stars`;
}

/**
 * The band header's anchor management: pick this band's exemplar from its own films.
 *
 * Only films already in the band are offered here, so this entry point can never hit the
 * designation mismatch - the film page is where a film is designated into a band it is
 * not currently in, and where the comparisons get to argue.
 */
function AnchorPicker({
  band,
  films,
  anchored,
  onChange,
}: {
  band: number;
  films: RatedFilm[];
  anchored: RatedFilm | null;
  onChange: () => void;
}) {
  const { busy, error, run } = useAsyncAction();
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button type="button" className="link-button" onClick={() => setOpen(true)}>
        {anchored ? `Anchor: ${anchored.title}` : "Set this band's anchor"}
      </button>
    );
  }
  return (
    <div className="anchor-picker">
      <label className="field">
        <span className="visually-hidden">{`Anchor for ${band.toFixed(1)}`}</span>
        <select
          disabled={busy}
          value={anchored?.tmdb_id ?? ""}
          onChange={(event) =>
            void run(async () => {
              const chosen = event.target.value;
              if (chosen === "") await api.retireAnchor(band);
              else await api.designate(band, Number(chosen));
              setOpen(false);
              onChange();
            })
          }
        >
          <option value="">No anchor</option>
          {films.map((film) => (
            <option key={film.tmdb_id} value={film.tmdb_id}>
              {film.title}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * One cell of the wall: a film under its rank, marked joint where the film is tied.
 *
 * Every film gets a cell of its own, tied or not, so the wall stays a single grid of
 * same-sized posters and the film after a tie group takes the next cell like any other.
 * That means the tie cannot be a box drawn around its members - a box would have to know
 * where the grid breaks its rows, and the column count follows the viewport. So the tie is
 * carried entirely by the stamp: the shared rank, marked shared on every member, and
 * nothing drawn between them (`styles.css`, "Films judged equal keep their own cells").
 *
 * `data-tie` is what the stamp's own styling keys on; the cell needs no other hook.
 */
function WallCell({
  film,
  tie = false,
  showBand = false,
}: {
  film: RatedFilm;
  tie?: boolean;
  showBand?: boolean;
}) {
  return (
    <li className="ordering-slot" data-tie={tie ? "true" : undefined}>
      <span className="ordering-rank">
        {/* A leaderboard's joint place. The equals sign is the whole mark on screen, and
            the word behind it is what a screen reader has instead. */}
        {tie && (
          <>
            <span className="visually-hidden">Joint </span>
            <span aria-hidden="true">=</span>
          </>
        )}
        {film.position}
      </span>
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
 * The band shows under a film only where the list is flat: inside the ordering's band
 * groups the value is already in the header above, and repeating it on every poster
 * would turn the header into decoration.
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
        {film.provisional && <SettlingMark film={film} />}
        {film.flagged && <span className="chip chip-flagged">Needs attention</span>}
      </div>
    </div>
  );
}

/**
 * The "settling" mark, which on the wall is also the door into settling that one film.
 *
 * On an anchor it stays a mark and nothing more. An anchor is re-placed only from its own
 * page, where the warning that landing outside its band retires it can be read before the
 * flow starts (rating-system.md) - and a badge is no place to put a warning.
 */
function SettlingMark({ film }: { film: RatedFilm }) {
  const navigate = useNavigate();
  const { busy, run } = useAsyncAction();

  if (film.anchor) return <ProvisionalMark />;
  return (
    <button
      type="button"
      className="provisional-mark settling-door"
      disabled={busy}
      title="Still settling: fewer comparisons than usual"
      // The word on screen is the film's state; the accessible name has to be the act,
      // because "settling" alone tells a screen reader nothing about what clicking does.
      aria-label={`Settle ${film.title} now`}
      onClick={() =>
        void run(async () => {
          await api.askToRePlace(film.tmdb_id);
          await navigate(placePath(film.tmdb_id));
        })
      }
    >
      settling
    </button>
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

function isEmpty(rated: RatedScreen): boolean {
  return (rated.groups?.length ?? rated.films?.length ?? 0) === 0;
}

function hasFilters(filters: RatedFilters): boolean {
  return Boolean(
    filters.bandMin || filters.bandMax || filters.genre || filters.decade || filters.flagged,
  );
}

/** The nudge points at the owner's best film, which is the easiest one to have an opinion about. */
function firstFilm(rated: RatedScreen): RatedFilm | undefined {
  return rated.groups?.[0]?.slots[0]?.[0] ?? rated.films?.[0];
}
