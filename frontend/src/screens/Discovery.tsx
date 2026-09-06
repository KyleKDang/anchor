import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { api, messageOf, type Feed, type Suggestion, type Threshold } from "../api";
import { Plot } from "../films/Plot";
import { Poster } from "../films/Poster";
import { filmPath, releaseYear } from "../films/tmdb";
import { plural, shortfall, worstBar } from "../films/unlock";

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
 */
export function Discovery() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (boundary: boolean) => {
    try {
      setFeed(await api.feed({ boundary }));
      // Cleared on success too, or one transient failure pins the banner for the session.
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  // Arriving is the session boundary the shelf changes at. Anything after that is the
  // same session, so the list cannot move while the owner is reading it.
  const arrived = useRef(false);
  useEffect(() => {
    void load(!arrived.current);
    if (!arrived.current) {
      // The same arrival clears the one-time dot this destination was carrying. A dot is
      // the quietest thing on the screen, so failing to clear one is not worth a banner;
      // the next visit asks again.
      void api.seenDiscovery().catch(() => undefined);
    }
    arrived.current = true;
  }, [load]);

  return (
    <>
      <h1>Discovery</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {feed !== null && (feed.unlocked ? <Shelf feed={feed} /> : <Locked feed={feed} />)}
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

/** The shelf itself, or the honest empty state when the engine has nothing to offer. */
function Shelf({ feed }: { feed: Feed }) {
  if (feed.films.length === 0) {
    return (
      <div className="empty">
        <p className="muted">
          Nothing to suggest just now. Anchor only puts a film here when it can say why, so
          this fills in as it learns more about what you like.
        </p>
      </div>
    );
  }
  return (
    <ul className="film-list">
      {feed.films.map((film) => (
        <Card key={film.tmdb_id} film={film} />
      ))}
    </ul>
  );
}

/**
 * One suggestion: the film, the reason, and the plot behind its spoiler toggle.
 *
 * The pitch is the loudest thing on the row after the title, because it is the only thing
 * the engine says out loud and the whole reason the shelf is worth reading. The plot stays
 * folded away, the way it does on every surface in Anchor that shows one.
 */
function Card({ film }: { film: Suggestion }) {
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
        </p>
        <p className="pitch">{film.pitch}</p>
        <Plot overview={film.overview} />
      </div>
    </li>
  );
}

function directed(directors: string[]): string {
  return directors.length > 0 ? `dir. ${directors.join(", ")}` : "";
}
