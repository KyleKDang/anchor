import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import { api, messageOf, type ImportCandidate, type ImportReviewRow } from "../../api";
import { Poster } from "../../films/Poster";
import { releaseYear } from "../../films/tmdb";
import { useAsyncAction } from "../../films/useAsyncAction";

/**
 * The rows the matcher would not decide, each one question: which film is this line?
 *
 * A list rather than a one-at-a-time wizard. The rows are independent and most are
 * obvious at a glance, so the owner can clear a dozen in one pass and leave the two they
 * are unsure about - and leaving them costs nothing, because a row nobody has answered
 * has no effect on the account at all.
 *
 * Each candidate leads with its poster, then its year and director, because on a
 * duplicate title and year the director is often the only thing telling two films apart.
 */
export function Review() {
  const [rows, setRows] = useState<ImportReviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows((await api.importReview()).rows);
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Drop the answered row here rather than re-reading: the rest have not changed. */
  function settled(rowId: string) {
    setRows((current) => current?.filter((row) => row.id !== rowId) ?? null);
  }

  return (
    <>
      <h1>Review your import</h1>
      <p className="muted">
        Anchor matched everything it was sure of. These lines could be more than one film, so they
        are yours to settle. Anything you leave affects nothing until you do.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {rows !== null && rows.length === 0 && (
        <div className="empty">
          <p className="muted">Nothing is waiting on you.</p>
          <p>
            <Link to="/profile">Back to your profile</Link>
          </p>
        </div>
      )}
      {rows !== null && rows.length > 0 && (
        <ul className="import-rows">
          {rows.map((row) => (
            <ReviewRow key={row.id} row={row} onSettled={() => settled(row.id)} />
          ))}
        </ul>
      )}
    </>
  );
}

function ReviewRow({ row, onSettled }: { row: ImportReviewRow; onSettled: () => void }) {
  const { busy, error, run } = useAsyncAction();

  return (
    <li className="import-row card">
      <div className="import-row-body">
        <p className="import-row-name">
          {row.name} <span className="muted">{releaseYear(row.year)}</span>
        </p>
        <p className="muted">{origin(row)}</p>
      </div>

      <ul className="import-candidates">
        {row.candidates.map((candidate) => (
          <li key={candidate.tmdb_id}>
            <button
              type="button"
              className="import-candidate"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.bindImportRow(row.id, candidate.tmdb_id);
                  onSettled();
                })
              }
            >
              <Poster title={candidate.title} path={candidate.poster_path} size="w154" />
              <span className="import-candidate-title">{candidate.title}</span>
              <span className="muted">{releaseYear(candidate.year)}</span>
              <span className="muted">{directors(candidate)}</span>
            </button>
          </li>
        ))}
      </ul>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="import-row-actions">
        {row.rescuable && (
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.rescueImportRow(row.id);
                onSettled();
              })
            }
          >
            Ask Letterboxd
          </button>
        )}
        <button
          type="button"
          className="link-button"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.dismissImportRow(row.id);
              onSettled();
            })
          }
        >
          None of these
        </button>
      </div>
    </li>
  );
}

/** What binding this row would do, so the choice is not made blind. */
function origin(row: ImportReviewRow): string {
  if (row.kind === "rating") return `You rated this ${row.rating?.toFixed(1) ?? "?"} on Letterboxd`;
  if (row.kind === "watchlist") return "From your watchlist";
  if (row.kind === "watched") return "You marked this watched";
  if (row.kind === "diary") return "From your diary";
  return "One of your favourites";
}

function directors(candidate: ImportCandidate): string {
  return candidate.directors.length > 0 ? candidate.directors.join(", ") : "Director unknown";
}
