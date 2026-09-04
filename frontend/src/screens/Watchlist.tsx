import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type Backlog,
  type BacklogFilm,
  type BacklogSort,
  type Threshold,
  type Tier,
  type TierFilm,
} from "../api";
import { MarkWatched } from "../films/MarkWatched";
import { Poster } from "../films/Poster";
import { filmPath, releaseYear } from "../films/tmdb";
import { useAsyncAction } from "../films/useAsyncAction";

const SORTS: { value: BacklogSort; label: string }[] = [
  { value: "added", label: "Recently added" },
  { value: "title", label: "Title" },
  { value: "year", label: "Year" },
];

/** The value of a filter select when it is not filtering. */
const ANY = "";

/**
 * The Watchlist screen: the ranked tier on top, the backlog below.
 *
 * Before the taste profile is ready the screen is honestly just the backlog, with a line
 * saying so and a thin bar saying how far off the unlock is. A tier ranked on popularity
 * would teach the owner on day one that the tier's opinion is worthless, so there is not
 * one.
 *
 * Nothing here shows a score or a rank number for an unwatched film, and nothing may
 * (ADR 0005): position is the whole of the engine's statement, and the order of the list
 * is how it is made. There is no sort by how good the engine thinks a film is either -
 * that would be a second, undamped ranked tier wearing a select.
 */
export function Watchlist() {
  const [sort, setSort] = useState<BacklogSort>("added");
  const [genre, setGenre] = useState(ANY);
  const [decade, setDecade] = useState(ANY);
  const [tier, setTier] = useState<Tier | null>(null);
  const [backlog, setBacklog] = useState<Backlog | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (boundary: boolean) => {
      try {
        // The tier first: reading it is what maintains it, so the backlog below is then
        // fetched against a list that has already settled and no film appears in both.
        setTier(await api.tier({ boundary }));
        setBacklog(
          await api.backlog({
            sort,
            genre: genre === ANY ? null : genre,
            decade: decade === ANY ? null : Number(decade),
          }),
        );
        // Cleared on success too, or one transient failure pins the banner for the session.
        setError(null);
      } catch (caught) {
        setError(messageOf(caught));
      }
    },
    [sort, genre, decade],
  );
  /** The screen after the owner's own action: what the action did, and nothing else. */
  const reload = useCallback(() => load(false), [load]);

  // Arriving is the session boundary the engine maintains the tier at. A re-read for a
  // sort or a filter is the same session, so it cannot move the list under the cursor.
  const arrived = useRef(false);
  useEffect(() => {
    void load(!arrived.current);
    arrived.current = true;
  }, [load]);

  return (
    <>
      <h1>Watchlist</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {tier !== null &&
        (tier.unlocked ? <Ranked tier={tier} onChange={reload} /> : <Locked tier={tier} />)}
      {backlog !== null && (
        <section className="section" aria-labelledby="backlog-heading">
          <h2 id="backlog-heading">Backlog</h2>
          {tier?.unlocked === true && backlog.films.length > 0 && (
            <p className="muted">Everything else you have added, in whatever order suits you.</p>
          )}
          {(backlog.genres.length > 0 || backlog.decades.length > 0) && (
            <div className="filters">
              <label className="field">
                <span>Sort by</span>
                <select
                  value={sort}
                  onChange={(event) => setSort(event.target.value as BacklogSort)}
                >
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
                  <option value={ANY}>All genres</option>
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
                  <option value={ANY}>All decades</option>
                  {backlog.decades.map((start) => (
                    <option key={start} value={String(start)}>
                      {start}s
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          {backlog.films.length === 0 ? (
            <div className="empty">
              <p className="muted">
                {tier?.unlocked === true && tier.up_next.length > 0 ? (
                  "Everything you have added is up in the ranked list."
                ) : (
                  <>
                    Nothing here yet. <Link to="/search">Search for a film</Link> to add one.
                  </>
                )}
              </p>
            </div>
          ) : (
            <ul className="film-list">
              {backlog.films.map((film) => (
                <Row
                  key={film.tmdb_id}
                  film={film}
                  ranked={tier?.unlocked === true}
                  onChange={reload}
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}

/**
 * The pre-gate half: what the ranked list is, why it is not here, and how far off it is.
 *
 * Ambient and nothing more. The bar is the loudness ceiling for this moment - the unlock
 * itself gets one dot and one line, and until then the screen simply explains itself.
 */
function Locked({ tier }: { tier: Tier }) {
  const share = tier.progress?.share ?? 0;
  return (
    <section className="section" aria-labelledby="ranked-heading">
      <h2 id="ranked-heading">Ranked list</h2>
      <p className="muted">
        Once Anchor knows your taste well enough to be worth reading, this is where your backlog
        gets ordered by what you are most likely to love next. It is not guessing before then, so
        below is simply your backlog.
      </p>
      <p className="unlock-bar">
        <span
          className="bar-track"
          role="img"
          aria-label={`${Math.round(share * 100)}% of the way`}
        >
          <span className="bar-fill" style={{ inlineSize: `${share * 100}%` }} />
        </span>
      </p>
      <p className="muted">
        {remaining(tier.progress?.thresholds ?? [])} <Link to="/profile">See what is left</Link>.
      </p>
    </section>
  );
}

/**
 * One line: the next thing worth doing about the unlock.
 *
 * The film count comes first whenever it is short, ahead of whichever bar is furthest
 * behind. Every other bar is a ratio over it, so on a young account they all read as
 * zero and the honest, actionable thing to say is how many more films to rate - "answer
 * more comparisons per film" is true there and no help at all.
 */
function remaining(thresholds: Threshold[]): string {
  const films = thresholds.find((one) => one.dimension === "rated_films");
  const worst =
    films !== undefined && share(films) < 1
      ? films
      : [...thresholds].sort((a, b) => share(a) - share(b))[0];
  if (worst === undefined) return "Keep rating films.";
  const short = Math.ceil(worst.need - worst.have);
  if (worst.dimension === "rated_films") {
    return `Rate ${short} more film${short === 1 ? "" : "s"} to unlock it.`;
  }
  if (worst.dimension === "bands_spanned") {
    return `Rate films across ${short} more half-star band${short === 1 ? "" : "s"} to unlock it.`;
  }
  if (worst.dimension === "comparisons_per_film") {
    return "Answer more comparisons per film to unlock it.";
  }
  return "Settle more of your library with your own comparisons to unlock it.";
}

function share(threshold: Threshold): number {
  return threshold.need === 0 ? 1 : Math.min(1, threshold.have / threshold.need);
}

/**
 * The tier itself: the up-next zone, the pool under it, and the vetoed list behind an
 * overflow.
 *
 * The two zones are named rather than numbered. Up next is a real statement and its order
 * is meant to be read; the pool is the rest of the top thirty and its order floats, so
 * putting ranks on either would promise a precision only one of them has.
 */
function Ranked({ tier, onChange }: { tier: Tier; onChange: () => void }) {
  return (
    <>
      <section className="section" aria-labelledby="up-next-heading">
        <h2 id="up-next-heading">Up next</h2>
        {tier.up_next.length === 0 ? (
          <div className="empty">
            <p className="muted">
              Nothing in your backlog yet. <Link to="/search">Search for a film</Link> to add one.
            </p>
          </div>
        ) : (
          <>
            <p className="muted">In order. Pin anything you want held at the top.</p>
            <ul className="film-list">
              {tier.up_next.map((film) => (
                <Row key={film.tmdb_id} film={film} ranked seated onChange={onChange} />
              ))}
            </ul>
          </>
        )}
      </section>

      {tier.pool.length > 0 && (
        <section className="section" aria-labelledby="pool-heading">
          <h2 id="pool-heading">In the running</h2>
          <p className="muted">
            The rest of what Anchor would put in front of you. Roughly ordered, not strictly.
          </p>
          <ul className="film-list">
            {tier.pool.map((film) => (
              <Row key={film.tmdb_id} film={film} ranked seated onChange={onChange} />
            ))}
          </ul>
        </section>
      )}

      {tier.vetoed.length > 0 && (
        <details className="spoiler section">
          <summary>Not from my queue ({tier.vetoed.length})</summary>
          <p className="muted">
            Kept out of the ranked list until you say otherwise. They are still in your backlog, and
            nothing about them has been marked down.
          </p>
          <ul className="film-list">
            {tier.vetoed.map((film) => (
              <Row key={film.tmdb_id} film={film} ranked onChange={onChange} />
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/**
 * One film, wherever it sits on this screen.
 *
 * The three overrides are quiet inline verbs rather than buttons. Their visible effect is
 * their whole confirmation (surfacing.md), a tier row carries all three at once, and
 * thirty rows of stacked buttons would be a wall of controls with the films lost in it.
 * Marking a film watched is the one thing on the row that is not queue management, and
 * the one thing that keeps a button.
 */
function Row({
  film,
  ranked,
  seated = false,
  onChange,
}: {
  film: BacklogFilm | TierFilm;
  /** The tier exists, so this row can offer to manage the queue. */
  ranked: boolean;
  /** This row holds a tier seat, so not-now has a seat to rotate out of. */
  seated?: boolean;
  onChange: () => void;
}) {
  const pinned = "pinned" in film && film.pinned;
  return (
    <li className="film-row">
      <Identity film={film} pinned={pinned}>
        {ranked && (
          <p className="row-verbs">
            {film.vetoed ? (
              <Verb
                label="Put back in the running"
                onClick={() => api.liftVeto(film.tmdb_id)}
                onDone={onChange}
              />
            ) : (
              <>
                {pinned ? (
                  <Verb label="Unpin" onClick={() => api.unpin(film.tmdb_id)} onDone={onChange} />
                ) : (
                  <Verb label="Pin" onClick={() => api.pin(film.tmdb_id)} onDone={onChange} />
                )}
                {/* Not-now rotates a seat out, so it says nothing on a film without one. */}
                {seated && !pinned && (
                  <Verb
                    label="Not now"
                    onClick={() => api.notNow(film.tmdb_id)}
                    onDone={onChange}
                  />
                )}
                {!pinned && (
                  <Verb
                    label="Not from my queue"
                    onClick={() => api.veto(film.tmdb_id)}
                    onDone={onChange}
                  />
                )}
              </>
            )}
          </p>
        )}
      </Identity>
      <div className="film-row-actions">
        <MarkWatched tmdbId={film.tmdb_id} onLater={onChange} />
      </div>
    </li>
  );
}

function Identity({
  film,
  pinned = false,
  children,
}: {
  film: BacklogFilm;
  pinned?: boolean;
  children?: ReactNode;
}) {
  return (
    <>
      <Link className="poster-link" to={filmPath(film.tmdb_id)} tabIndex={-1} aria-hidden="true">
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
          {pinned && <span className="state-flag">Pinned</span>}
          {film.vetoed && <span className="state-flag">Not from my queue</span>}
        </p>
        {children}
      </div>
    </>
  );
}

/** One override: a quiet verb whose visible effect on the list is its confirmation. */
function Verb({
  label,
  onClick,
  onDone,
}: {
  label: string;
  onClick: () => Promise<void>;
  onDone: () => void;
}) {
  const { busy, error, run } = useAsyncAction();
  return (
    <>
      <button
        type="button"
        className="link-button"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            await onClick();
            onDone();
          })
        }
      >
        {label}
      </button>
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}
