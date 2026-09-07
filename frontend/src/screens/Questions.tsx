import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api, messageOf, type CriteriaCard } from "../api";
import { CriteriaQuestion } from "../films/Criteria";
import { filmPath } from "../films/tmdb";

/**
 * The criteria session: "answer questions about this film", opened from its page.
 *
 * A full-screen stream of cards about one film against varied opponents, outside the
 * app frame like the picker, with a leave control always visible and the count of
 * answers given as the only thing it says about itself (screens-and-flows.md). It is
 * open-ended: it runs until the owner leaves or Anchor has nothing left to ask that it
 * has not asked before, and it is available whatever the frequency setting says.
 *
 * Leaving sends nothing. The card on screen already reads as unanswered, which is all a
 * session ever leaves behind.
 */
export function Questions() {
  const { tmdbId } = useParams();
  const id = Number(tmdbId);
  const [title, setTitle] = useState<string | null>(null);
  const [first, setFirst] = useState<CriteriaCard | null>(null);
  const [opened, setOpened] = useState(false);
  const [answered, setAnswered] = useState(0);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.film(id), api.openCriteriaSession(id)])
      .then(([film, session]) => {
        if (cancelled) return;
        setTitle(film.title);
        setFirst(session.card);
        setOpened(true);
      })
      .catch((caught: unknown) => !cancelled && setError(messageOf(caught)));
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="place questions">
      {/* The bar never leaves: the way out on one side, the count of answers on the
          other, and nothing else the session says about itself. */}
      <header className="questions-bar">
        <p className="back-link questions-leave">
          <Link to={filmPath(id)}>Leave</Link>
        </p>
        <p className="muted questions-count" aria-live="polite">
          {answered === 1 ? "1 answer" : `${answered} answers`}
        </p>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!opened && !error && <p className="muted">Loading…</p>}
      {opened && first !== null && !over && (
        <CriteriaQuestion
          first={first}
          tag={title === null ? "About this film" : `About ${title}`}
          dismissLabel="Skip this one"
          dismissal="passes"
          onAnswered={() => setAnswered((count) => count + 1)}
          onDone={() => setOver(true)}
        />
      )}
      {opened && (first === null || over) && (
        <p className="muted questions-over">
          {first === null
            ? "There is nothing to ask about it yet."
            : "That is everything Anchor can think to ask about it."}{" "}
          <Link to={filmPath(id)}>Back to the film</Link>
        </p>
      )}
    </div>
  );
}
