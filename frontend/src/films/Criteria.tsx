import { useState } from "react";

import { api, type CriteriaCard, type CriteriaVerdict } from "../api";
import { Poster } from "./Poster";
import { releaseYear } from "./tmdb";
import { useAsyncAction } from "./useAsyncAction";

/**
 * One criteria question, and the run or session it belongs to.
 *
 * Two homes, one card (screens-and-flows.md): the run under a landing and the session
 * from a film's page both show this and nothing else. The card shows the two films side
 * by side, the question, the two films as answers, "about the same", and a small dismiss.
 *
 * Each answer hands back the next card in the same home, which slides in where this one
 * was; when nothing comes back the home is over and the card goes. The offer already
 * reads as unanswered, and walking away is recorded exactly the same way as dismissing,
 * so the card says nothing congratulatory afterwards. What the small dismiss does is the
 * home's call, and the home is the one thing a caller says: the run ends on it and sends
 * nothing, while the session passes the question over and takes the next.
 *
 * The wording is a fixed template with the quality dropped in. Anchor never invents the
 * question, and this component is the only place the template exists.
 */
/** The two homes, and what the small dismiss means in each (screens-and-flows.md). */
const HOMES = {
  run: { dismissLabel: "No thanks", dismissal: "ends" },
  session: { dismissLabel: "Skip this one", dismissal: "passes" },
} as const;

export function CriteriaQuestion({
  first,
  home,
  tag,
  onAnswered,
  onDone,
}: {
  /** The card the home opened with. */
  first: CriteriaCard;
  home: keyof typeof HOMES;
  /** The small line above the question, saying what this is. */
  tag: string;
  /** An answer landed; the count is the home's to show. */
  onAnswered?: () => void;
  /** The home is over: it ran out of questions, or the owner dismissed the run. */
  onDone: () => void;
}) {
  const { busy, error, run } = useAsyncAction();
  const [card, setCard] = useState<CriteriaCard>(first);
  // A card that arrived after the first slides in; the first one was there already.
  const entered = card.id !== first.id;
  const { dismissLabel, dismissal } = HOMES[home];

  const follow = (next: CriteriaCard | null) => (next === null ? onDone() : setCard(next));

  const answer = (verdict: CriteriaVerdict) =>
    void run(async () => {
      const dealt = await api.answerCriteria(card.id, verdict);
      onAnswered?.();
      follow(dealt.card);
    });

  const dismiss = () => {
    if (dismissal === "ends") onDone();
    else void run(async () => follow((await api.dismissCriteria(card.id)).card));
  };

  return (
    <section
      key={card.id}
      className={entered ? "criteria criteria-enter" : "criteria"}
      aria-labelledby="criteria-heading"
    >
      <p className="criteria-tag">{tag}</p>
      <h2 id="criteria-heading">Which had the better {card.quality.toLowerCase()}?</h2>
      <div className="criteria-films">
        {[
          { film: card.film_a, verdict: "a" as const },
          { film: card.film_b, verdict: "b" as const },
        ].map(({ film, verdict }) => (
          <button
            key={film.tmdb_id}
            type="button"
            className="criteria-film"
            disabled={busy}
            onClick={() => answer(verdict)}
          >
            <span className="criteria-film-body">
              <Poster title={film.title} path={film.poster_path} size="w154" />
              <span className="film-title">{film.title}</span>
              <span className="muted">{releaseYear(film.year)}</span>
            </span>
          </button>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="criteria-actions">
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => answer("tied")}
        >
          About the same
        </button>
        <button type="button" className="link-button" disabled={busy} onClick={dismiss}>
          {dismissLabel}
        </button>
      </div>
    </section>
  );
}
