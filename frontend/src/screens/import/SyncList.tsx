import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { api, messageOf, type SyncFilm, type SyncList as SyncListData } from "../../api";
import { Band } from "../../films/Band";
import { Poster } from "../../films/Poster";
import { filmPath, releaseYear } from "../../films/tmdb";
import { useAsyncAction } from "../../films/useAsyncAction";

/**
 * The sync list: the films the owner would have to retype on Letterboxd today.
 *
 * Anchor never writes to Letterboxd, so this is a worksheet rather than a queue. It is
 * read top to bottom with the other tab open, and its whole job is to say the two things
 * a person retyping needs: which film, and what to change it to.
 *
 * Ambient by ADR 0011's rules. It is here and nowhere else, it counts rather than nags,
 * and it renders nothing at all when there is nothing to carry over - which is the state
 * a freshly imported account is in, and the state an owner who keeps up returns to.
 */
export function SyncList() {
  const [list, setList] = useState<SyncListData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setList(await api.syncList());
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (error !== null) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (list === null || list.count === 0) return null;

  return (
    <div className="sync">
      <h3>{count(list.count)} to carry over</h3>
      <p className="muted">
        Anchor never writes to Letterboxd. Change these there yourself, then mark them off here
        so Anchor knows the two agree again.
      </p>
      {list.changed.length > 0 && (
        <Section films={list.changed} onMarked={reload} heading="Ratings that have moved">
          Change these on Letterboxd to match.
        </Section>
      )}
      {list.never_recorded.length > 0 && (
        <Section films={list.never_recorded} onMarked={reload} heading="Not on Letterboxd yet">
          You rated these in Anchor, so Letterboxd has no entry to change - add them.
        </Section>
      )}
      {/* One film is not a list to sweep, and the row's own button is right there. */}
      {list.count > 1 && <MarkAll onMarked={reload} />}
    </div>
  );
}

function Section({
  films,
  heading,
  children,
  onMarked,
}: {
  films: SyncFilm[];
  heading: string;
  children: ReactNode;
  onMarked: () => Promise<void>;
}) {
  return (
    <>
      <h4 className="sync-heading">{heading}</h4>
      <p className="muted">{children}</p>
      <ul className="film-list">
        {films.map((film) => (
          <Row key={film.tmdb_id} film={film} onMarked={onMarked} />
        ))}
      </ul>
    </>
  );
}

/**
 * One film, old → new.
 *
 * The arrow is the whole row: the owner is looking for the value to type, and the old one
 * is there only so they can recognise the entry they are editing. A film Letterboxd never
 * saw has no old value, so the arrow is dropped rather than drawn from a blank.
 */
function Row({ film, onMarked }: { film: SyncFilm; onMarked: () => Promise<void> }) {
  const { busy, error, run } = useAsyncAction();

  return (
    <li className="film-row">
      <Link className="poster-link" to={filmPath(film.tmdb_id)} tabIndex={-1} aria-hidden="true">
        <Poster title={film.title} path={film.poster_path} size="w154" />
      </Link>
      <div className="film-row-body">
        {/* A paragraph rather than a heading: the row is one line of a worksheet, and a
            fifth heading level under Profile's would be structure that means nothing. */}
        <p className="film-row-title">
          <Link to={filmPath(film.tmdb_id)}>{film.title}</Link>
        </p>
        {/* The arrow carries the whole meaning visually and says nothing at all aloud, so
            each value states which side it is: two bare star counts in a row would be
            read out as one indistinguishable from the other. */}
        <p className="film-row-meta">
          <span className="muted">{releaseYear(film.year)}</span>
          {film.synced !== null && (
            <>
              <span className="sync-was">
                <span className="visually-hidden">Letterboxd has </span>
                <Band band={film.synced} />
              </span>
              <span className="sync-arrow" aria-hidden="true">
                →
              </span>
            </>
          )}
          <span>
            <span className="visually-hidden">
              {film.synced === null ? "you rated it " : "change it to "}
            </span>
            <Band band={film.band} />
          </span>
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
          className="button secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.markSynced(film.tmdb_id);
              await onMarked();
            })
          }
        >
          Synced
        </button>
      </div>
    </li>
  );
}

/** For the owner who has just worked the whole list; the per-film button is the default. */
function MarkAll({ onMarked }: { onMarked: () => Promise<void> }) {
  const { busy, error, run } = useAsyncAction();

  return (
    <p className="sync-all">
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
      <button
        type="button"
        className="link-button"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            await api.markAllSynced();
            await onMarked();
          })
        }
      >
        Mark them all synced
      </button>
    </p>
  );
}

function count(films: number): string {
  return `${films} ${films === 1 ? "film" : "films"}`;
}
