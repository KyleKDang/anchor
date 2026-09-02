import { useState } from "react";
import { useNavigate } from "react-router";

import { api, type Rate } from "../api";
import { placePath } from "./tmdb";
import { useAsyncAction } from "./useAsyncAction";

/**
 * Logging a watch, wherever a film is shown: a row, or the film page.
 *
 * The offer is the point. Marking watched is never a bare button, because the owner has
 * to answer rate-now-or-later before anything is recorded - so the single button opens
 * the two choices in place, primary and quiet secondary, rather than picking for them.
 * Escape backs out, since the whole thing can be opened by a misclick.
 */
export function MarkWatched({
  tmdbId,
  onLater,
  label = "Mark watched",
}: {
  tmdbId: number;
  /** Called after "later"; "now" leaves for the placement flow instead. */
  onLater: () => void;
  label?: string;
}) {
  const [choosing, setChoosing] = useState(false);
  const { busy, error, run } = useAsyncAction();
  const navigate = useNavigate();

  async function choose(rate: Rate) {
    await run(async () => {
      await api.markWatched(tmdbId, rate);
      if (rate === "now") navigate(placePath(tmdbId));
      else onLater();
    });
  }

  if (!choosing) {
    return (
      <button type="button" className="button secondary" onClick={() => setChoosing(true)}>
        {label}
      </button>
    );
  }

  return (
    <div
      className="rate-choice"
      role="group"
      aria-labelledby={`rate-choice-${tmdbId}`}
      onKeyDown={(event) => event.key === "Escape" && setChoosing(false)}
    >
      {/* Visible, not just an accessible name: without it the two buttons read as two
          unrelated verbs sitting among the row's others. */}
      <span className="rate-choice-label muted" id={`rate-choice-${tmdbId}`}>
        Rate it now?
      </span>
      <button type="button" className="button" disabled={busy} onClick={() => void choose("now")}>
        Rate now
      </button>
      <button
        type="button"
        className="button secondary"
        disabled={busy}
        onClick={() => void choose("later")}
      >
        Later
      </button>
      <button type="button" className="link-button" disabled={busy} onClick={() => setChoosing(false)}>
        Cancel
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
