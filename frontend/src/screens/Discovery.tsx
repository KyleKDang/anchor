import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type Acted,
  type DismissedFilm,
  type Feed,
  type Suggestion,
  type Threshold,
} from "../api";
import { useAuth } from "../auth";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { filmPath, placePath, releaseYear } from "../films/tmdb";
import { plural, shortfall, worstBar } from "../films/unlock";
import { useAsyncAction } from "../films/useAsyncAction";

/**
 * The account whose feed this app load has already opened, if any.
 *
 * Module-level rather than a ref, and that is the whole of what makes a boundary a
 * boundary. A ref lives and dies with the component, so every trip out to a film page and
 * back would count as a fresh arrival - and an arrival advances the refresh counter that
 * rotation is denominated in, which would let an owner browse their own shelf away in one
 * sitting. What discovery.md means by a session boundary is the next app open, so that is
 * what this measures.
 *
 * Keyed by account rather than a bare flag, so logging out and in as somebody else is
 * their arrival and not a continuation of the last person's.
 */
let openedFor: string | null = null;

/**
 * The Discovery screen: films from the wider catalog, chosen for this owner.
 *
 * A flat shelf of about twenty, ordered by the engine, each carrying the one sentence it
 * is willing to say about why the film is there. There are no themed rows, no fit badges,
 * no percentages and no scores - position is the whole of the statement (ADR 0005), and
 * the sentence is grounded in films the owner already loved rather than in a number.
 *
 * The shelf runs short whenever the engine has less to stand behind, and says nothing
 * about it. There is no "we could not find more" banner and nothing is padded out to
 * twenty: a feed that only shows what it can defend has nothing to apologise for.
 *
 * Every card carries three ways off it, and keeping them apart is the point. Adding one
 * to the backlog says "I want this"; seen-it says "I already have"; not-interested says
 * "the pitch does not appeal" - and it only means that reliably because the other two
 * exist to catch the answers that would otherwise be filed under it. The slot behind
 * whichever one is used fills itself at once, so the shelf never has a hole in it.
 */
export function Discovery() {
  const { account } = useAuth();
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invited, setInvited] = useState<Suggestion | null>(null);

  const load = useCallback(async (boundary: boolean) => {
    try {
      setFeed(await api.feed({ boundary }));
      // Cleared on success too, or one transient failure pins the banner for the session.
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  useEffect(() => {
    const id = account?.id;
    if (id === undefined) return;
    const arriving = openedFor !== id;
    if (arriving) openedFor = id;
    void load(arriving);
    if (arriving) {
      // The same arrival clears the one-time dot this destination was carrying. A dot is
      // the quietest thing on the screen, so failing to clear one is not worth a banner;
      // the next visit asks again.
      void api.seenDiscovery().catch(() => undefined);
    }
  }, [load, account?.id]);

  /**
   * Take the shelf an action handed back, rather than reloading the screen for it.
   *
   * The response already is the shelf with the gap closed and the slot refilled, so a
   * reload would ask the same question twice and let the list flicker between the two
   * answers. An action is never a session boundary, so nothing the engine did can arrive
   * through here either.
   */
  const applied = useCallback((acted: Acted, film: Suggestion) => {
    setFeed((standing) => (standing === null ? standing : { ...standing, films: acted.films }));
    if (acted.place_now) setInvited(film);
  }, []);

  return (
    <>
      <h1>Discovery</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {invited !== null && <PlaceNow film={invited} onSkip={() => setInvited(null)} />}
      {feed !== null &&
        (feed.unlocked ? <Shelf feed={feed} onActed={applied} /> : <Locked feed={feed} />)}
    </>
  );
}

/**
 * The pre-gate half: what this screen is for, and what it is still waiting on.
 *
 * A sentence and a line, and deliberately no progress bar - surfacing.md gives the bar to
 * the pre-gate Watchlist and asks only that this screen explain itself, so drawing one
 * here would make the quieter of the two unlocks the louder of them.
 *
 * Discovery could fill this space with popular films the day an account is created, and
 * that is exactly what it must not do: a shelf assembled from no signal teaches the owner
 * on day one that the engine's opinion is worth nothing.
 */
function Locked({ feed }: { feed: Feed }) {
  return (
    <section className="section" aria-labelledby="shelf-heading">
      <h2 id="shelf-heading">Suggestions</h2>
      <p className="muted">
        Once Anchor has a feel for your taste, this is where it puts films you have never
        added - each with the reason it thinks you will want it. It will not guess before
        then, so there is nothing here yet.
      </p>
      <p className="muted">
        {remaining(feed.progress?.thresholds ?? [])} <Link to="/profile">See what is left</Link>.
      </p>
    </section>
  );
}

/** One line: the next thing worth doing about the unlock, in this screen's own words. */
function remaining(thresholds: Threshold[]): string {
  const worst = worstBar(thresholds);
  if (worst === undefined) return "Keep rating films.";
  const short = shortfall(worst);
  if (worst.dimension === "bands_spanned") {
    return `Rate films across ${short} more half-star band${plural(short)} first.`;
  }
  return `Rate ${short} more film${plural(short)} first.`;
}

/** What every action on a card reports back: the new shelf, and the card it was about. */
type Applied = (acted: Acted, film: Suggestion) => void;

/** The shelf itself, or the honest empty state when the engine has nothing to offer. */
function Shelf({ feed, onActed }: { feed: Feed; onActed: Applied }) {
  return (
    <>
      {feed.films.length === 0 ? (
        <div className="empty">
          <p className="muted">
            Nothing to suggest just now. Anchor only puts a film here when it can say why, so
            this fills in as it learns more about what you like.
          </p>
        </div>
      ) : (
        <ul className="film-list">
          {feed.films.map((film) => (
            <Card key={film.tmdb_id} film={film} onActed={onActed} />
          ))}
        </ul>
      )}
      <NotInterested />
    </>
  );
}

/**
 * One suggestion: the film, the reason, the plot behind its spoiler toggle, and the three
 * things the owner can say about it.
 *
 * The pitch is the loudest thing on the row after the title, because it is the only thing
 * the engine says out loud and the whole reason the shelf is worth reading. The plot stays
 * folded away, the way it does on every surface in Anchor that shows one.
 *
 * Adding to the backlog is the one action with a button, because it is the one the shelf
 * exists for. The other two are quiet verbs under the pitch, the shape the ranked tier's
 * overrides already take: twenty rows with three buttons each would be sixty controls
 * with the films lost among them.
 */
function Card({ film, onActed }: { film: Suggestion; onActed: Applied }) {
  return (
    <li className="film-row suggestion">
      <Link className="poster-link" to={filmPath(film.tmdb_id)} tabIndex={-1} aria-hidden="true">
        <Poster title={film.title} path={film.poster_path} size="w154" />
      </Link>
      <div className="film-row-body">
        <h3 className="film-row-title">
          <Link to={filmPath(film.tmdb_id)}>{film.title}</Link>
        </h3>
        <p className="film-row-meta">
          <span className="muted">
            {[releaseYear(film.year), directed(film.directors), film.genres.join(", ")]
              .filter(Boolean)
              .join(" · ")}
          </span>
          {/* Freshness, not fit: it says the card is new to this visit and nothing about
              how good a match it is, which is why it can sit here at all (ADR 0005). */}
          {film.fresh && <span className="state-flag">New</span>}
        </p>
        <p className="pitch">{film.pitch}</p>
        <Plot overview={film.overview} />
        <p className="row-verbs">
          <Answer
            label="Seen it"
            act={() => api.seenSuggestion(film.tmdb_id)}
            film={film}
            onActed={onActed}
          />
          <Answer
            label="Not interested"
            act={() => api.dismissSuggestion(film.tmdb_id)}
            film={film}
            onActed={onActed}
          />
        </p>
      </div>
      <div className="film-row-actions">
        <Answer
          label="Add to backlog"
          act={() => api.acceptSuggestion(film.tmdb_id)}
          film={film}
          onActed={onActed}
          weight="button"
        />
      </div>
    </li>
  );
}

/**
 * One of the three answers. They differ in weight and wording and in nothing else.
 *
 * Written once because they behave identically: each one asks the server, and the card
 * leaving the shelf is the whole of the confirmation (surfacing.md). Only the affirmative
 * answer takes the button, because it is the one the shelf exists for.
 */
function Answer({
  label,
  act,
  film,
  onActed,
  weight = "link-button",
}: {
  label: string;
  act: () => Promise<Acted>;
  film: Suggestion;
  onActed: Applied;
  weight?: "button" | "link-button";
}) {
  const { busy, error, run } = useAsyncAction();
  return (
    <>
      <button
        type="button"
        className={weight}
        disabled={busy}
        onClick={() => void run(async () => onActed(await act(), film))}
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

/**
 * The seen-it invite: one line, at a moment the owner triggered, and skippable.
 *
 * A line rather than a modal, because nothing in Anchor interrupts (ADR 0011) - and
 * skipping it costs the owner nothing at all, since marking the film seen already put it
 * in the rate-later queue. "Later" never becomes a promise, so there is no chaser and
 * this never comes back on its own.
 */
function PlaceNow({ film, onSkip }: { film: Suggestion; onSkip: () => void }) {
  return (
    <p className="nudge">
      Marked <strong>{film.title}</strong> as watched. It is waiting in your rate-later queue.{" "}
      <Link to={placePath(film.tmdb_id)}>Rate it now</Link>, or{" "}
      <button type="button" className="link-button" onClick={onSkip}>
        leave it for later
      </button>
      .
    </p>
  );
}

/**
 * The dismissed list, behind the Discovery overflow.
 *
 * A record to check rather than something to act on, so it lives folded away under the
 * shelf - the loudness ceiling for anything that is not the shelf itself (surfacing.md).
 * It is fetched only when the owner opens it, because a list nobody looks at is not worth
 * a request on every arrival.
 *
 * The wording never reads as distaste. A dismissal says the pitch did not land, which is
 * the whole reason seen-it is a separate action: this list means only that.
 */
function NotInterested() {
  const [films, setFilms] = useState<DismissedFilm[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setFilms((await api.dismissedSuggestions()).films);
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  return (
    <details
      className="spoiler section"
      onToggle={(event) => event.currentTarget.open && void load()}
    >
      <summary>Not interested{films && films.length > 0 ? ` (${films.length})` : ""}</summary>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {/* Three states, and the first one is the frame between opening the overflow and
          the list arriving. Saying nothing there is the point: the alternative is a
          sentence about films that are not on screen yet. */}
      {films === null ? null : films.length === 0 ? (
        <p className="muted">
          Nothing here yet. Films you turn down are kept on this list, and you can put any of
          them back whenever you like.
        </p>
      ) : (
        <>
          <p className="muted">
            Kept off the shelf until you say otherwise. Nothing about them has been marked down.
          </p>
          <ul className="film-list">
            {films.map((film) => (
              <DismissedRow key={film.tmdb_id} film={film} onChanged={load} />
            ))}
          </ul>
        </>
      )}
    </details>
  );
}

/** One dismissed film, with the inverse of the action that put it here (surfacing.md). */
function DismissedRow({ film, onChanged }: { film: DismissedFilm; onChanged: () => void }) {
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
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.liftDismissal(film.tmdb_id);
              onChanged();
            })
          }
        >
          Put back on the shelf
        </button>
      </div>
    </li>
  );
}

function directed(directors: string[]): string {
  return directors.length > 0 ? `dir. ${directors.join(", ")}` : "";
}
