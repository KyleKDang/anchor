import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link } from "react-router";

import {
  api,
  messageOf,
  type ImportState,
  type ImportUnmatchedRow,
  type ImportWarning,
  type Unlock,
} from "../../api";
import { releaseYear } from "../../films/tmdb";
import { useAsyncAction } from "../../films/useAsyncAction";
import { SyncList } from "./SyncList";

/** How often the progress line re-reads while matching runs. Nothing else polls. */
const POLL_MS = 2000;

/**
 * Profile's Letterboxd area: the import, what it left behind, and the hard reset.
 *
 * Everything here is ambient by ADR 0011's rules. The residue counts sit at the import's
 * own entry point and are mentioned nowhere else in the app, the review queue is a link
 * rather than a nag, and nothing opens itself.
 *
 * The one moving part is the progress line, which polls while the matcher works. That is
 * not the engine narrating its background work: the owner has just handed over a file and
 * is standing here waiting to hear what became of it.
 */
export function Letterboxd() {
  const [state, setState] = useState<ImportState | null>(null);
  const [warning, setWarning] = useState<ImportWarning | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The two are read together, because what a re-import would destroy changes exactly
  // when the import does: a warning fetched once on mount enumerates the empty account
  // the owner had a second before the matcher filled it.
  const reload = useCallback(async () => {
    try {
      const [next, destroys] = await Promise.all([api.importState(), api.importWarning()]);
      setState(next);
      setWarning(destroys);
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const matching = state?.status === "matching";
  useEffect(() => {
    if (!matching) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [matching, reload]);

  return (
    <section className="section" aria-labelledby="letterboxd-heading">
      <h2 id="letterboxd-heading">Letterboxd</h2>
      <p className="muted">
        Anchor never writes to Letterboxd. A one-time import of your account export is the only
        data that crosses over.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {/* Ahead of the import's own residue, and outside the has-imported gate: a film
          rated in Anchor is one to carry over whether or not an export ever landed. */}
      <SyncList />
      {state?.status === "none" && <Upload warning={warning} onDone={reload} />}
      {state !== null && state.status !== "none" && (
        <>
          <Summary state={state} />
          <Residue state={state} onChanged={reload} />
          {/* Not while the import is still landing: mid-match the counts climb under a
              destructive button, and a moving enumeration is misleading however true
              each reading is. The matching notice carries that window instead. */}
          {warning !== null && state.status !== "matching" && (
            <Reimport warning={warning} onDone={reload} />
          )}
        </>
      )}
    </section>
  );
}

/** What the export held, once it has been read. Counts, not a progress narrative. */
function Summary({ state }: { state: ImportState }) {
  const { counts } = state;
  const read = [
    plural(counts.rating, "rating"),
    plural(counts.watchlist, "watchlist film"),
    plural(counts.watched, "watched film"),
    plural(counts.diary, "diary entry", "diary entries"),
  ].filter((part) => part !== null);

  return (
    <>
      <p>
        Imported from <strong>{state.source_name}</strong>.
      </p>
      <p className="muted">{read.length > 0 ? `${read.join(", ")}.` : "Nothing to import."}</p>
      {state.status === "matching" && (
        <p className="notice" role="status">
          Matching against TMDB: {total(state) - state.pending} of {total(state)} rows so far. You
          can carry on using Anchor while this finishes.
        </p>
      )}
      <Unlocked unlocked={state.unlocked} />
    </>
  );
}

const UNLOCKED: Record<Unlock, ReactNode> = {
  discovery: <Link to="/discovery">Discovery</Link>,
  watchlist: <Link to="/watchlist">a ranked watchlist</Link>,
};

/**
 * What the import just unlocked, named on the screen that earned it.
 *
 * An export of any real size crosses both readiness bars the moment matching completes,
 * and saying so is the point of importing (onboarding-and-import.md). It is the same
 * moment the nav's dots light, and this line is the other half of it: it appears once,
 * empties as the owner visits each screen, and is never repeated (ADR 0011).
 */
function Unlocked({ unlocked }: { unlocked: Unlock[] }) {
  if (unlocked.length === 0) return null;
  const named = unlocked.map((unlock) => UNLOCKED[unlock]);
  return (
    <p className="nudge">
      That was enough to go on. You have{" "}
      {named.map((name, index) => (
        <Fragment key={index}>
          {index > 0 && (index === named.length - 1 ? " and " : ", ")}
          {name}
        </Fragment>
      ))}{" "}
      from here.
    </p>
  );
}

/**
 * What the import could not settle by itself: the review queue and the unmatched list.
 *
 * Both live here and nowhere else. An unmatched row has no effect on the account at all
 * until it is bound or dismissed, so it is a list to visit rather than a thing to be
 * reminded about.
 */
function Residue({ state, onChanged }: { state: ImportState; onChanged: () => Promise<void> }) {
  if (state.status === "matching") return null;
  if (state.review_pending === 0 && state.unmatched === 0) {
    return <p className="muted">Every row found its film.</p>;
  }
  return (
    <>
      {state.review_pending > 0 && (
        <p className="nudge">
          <Link to="/import/review">
            {plural(state.review_pending, "film needs a look", "films need a look")}
          </Link>
        </p>
      )}
      {state.unmatched > 0 && <Unmatched count={state.unmatched} onChanged={onChanged} />}
    </>
  );
}

/** The films that found nothing, behind a disclosure: they are not waiting on anybody. */
function Unmatched({ count, onChanged }: { count: number; onChanged: () => Promise<void> }) {
  const [rows, setRows] = useState<ImportUnmatchedRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows((await api.importUnmatched()).rows);
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, []);

  return (
    <details className="spoiler" onToggle={(event) => event.currentTarget.open && void load()}>
      <summary>
        {plural(count, "film Anchor could not find", "films Anchor could not find")}
      </summary>
      <p className="muted">
        These affect nothing. Letterboxd hosts some entries TMDB keeps on its TV side, and a film
        deleted since resolves to nothing at all.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {rows !== null && (
        <ul className="import-rows">
          {rows.map((row) => (
            <UnmatchedRow
              key={row.id}
              row={row}
              onResolved={async () => {
                await load();
                await onChanged();
              }}
            />
          ))}
        </ul>
      )}
    </details>
  );
}

function UnmatchedRow({
  row,
  onResolved,
}: {
  row: ImportUnmatchedRow;
  onResolved: () => Promise<void>;
}) {
  const { busy, error, run } = useAsyncAction();

  return (
    <li className="import-row">
      <div className="import-row-body">
        <p className="import-row-name">
          {row.name} <span className="muted">{releaseYear(row.year)}</span>
        </p>
        <p className="muted">{describe(row)}</p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="import-row-actions">
        {row.rescuable && (
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.rescueImportRow(row.id);
                await onResolved();
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
              await onResolved();
            })
          }
        >
          Forget this film
        </button>
      </div>
    </li>
  );
}

/**
 * The first import: a file, a word about where to find it, and what it will cost.
 *
 * An owner who tried Anchor before their export was ready holds films no import put
 * there, and importing erases them like any other reset - there is no merge path, ever.
 * So the cost is shown wherever there is a cost, rather than only once an import has
 * run: what makes this dangerous is the account holding something, not its history.
 */
function Upload({
  warning,
  onDone,
}: {
  warning: ImportWarning | null;
  onDone: () => Promise<void>;
}) {
  const atRisk = warning !== null && holds(warning) ? warning : null;

  return (
    <>
      <p className="muted">
        On Letterboxd, open Settings, then Data, and choose Export your data. Upload the zip it
        gives you, unchanged.
      </p>
      {atRisk === null ? (
        <ExportForm label="Import your export" onDone={onDone} />
      ) : (
        <div className="danger-zone">
          <h3>You have already started this account</h3>
          <p className="muted">
            Importing wipes this account and rebuilds it from the export alone. There is no merge.
            This erases {destroyed(atRisk)}. There is no undo.
          </p>
          <ExportForm
            label="Erase and import"
            danger
            confirmPhrase={atRisk.confirmation_required ? atRisk.confirmation_phrase : null}
            onDone={onDone}
          />
        </div>
      )}
    </>
  );
}

/**
 * Replacing an export, which is a hard reset and reads like one.
 *
 * There is no merge path, ever, so the warning enumerates what will actually go rather
 * than saying "your data": a count is something the owner can weigh. Once they have
 * answered enough comparisons for the log to be worth protecting they type the phrase as
 * well, because somewhere around there the counts stop carrying the weight by themselves.
 */
function Reimport({ warning, onDone }: { warning: ImportWarning; onDone: () => Promise<void> }) {
  return (
    <div className="danger-zone">
      <h3>Replace with a new export</h3>
      <p className="muted">
        Importing again wipes this account and rebuilds it from the new export alone. There is no
        merge. This erases {destroyed(warning)}. There is no undo.
      </p>
      <ExportForm
        label="Replace everything"
        danger
        confirmPhrase={warning.confirmation_required ? warning.confirmation_phrase : null}
        onDone={onDone}
      />
    </div>
  );
}

function ExportForm({
  label,
  danger = false,
  confirmPhrase = null,
  onDone,
}: {
  label: string;
  danger?: boolean;
  confirmPhrase?: string | null;
  onDone: () => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [typed, setTyped] = useState("");
  const { busy, error, run } = useAsyncAction();

  const typedRight = confirmPhrase === null || typed.trim().toLowerCase() === confirmPhrase;
  const blocked = file === null || busy || !typedRight;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (file === null) return;
    await run(async () => {
      await api.uploadExport(file, confirmPhrase === null ? undefined : typed.trim());
      setFile(null);
      setTyped("");
      if (input.current !== null) input.current.value = "";
      await onDone();
    });
  }

  return (
    <form className="form" onSubmit={submit}>
      <label className="field">
        <span>Your Letterboxd export (.zip)</span>
        <input
          ref={input}
          type="file"
          name="export"
          accept=".zip,application/zip"
          required
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      {confirmPhrase !== null && (
        <label className="field">
          <span>
            Type <strong>{confirmPhrase}</strong> to confirm
          </span>
          <input
            type="text"
            name="confirm"
            autoComplete="off"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
        </label>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className={danger ? "button danger" : "button"} disabled={blocked}>
        {label}
      </button>
    </form>
  );
}

// --- Wording ---

function total(state: ImportState): number {
  const { counts } = state;
  return counts.rating + counts.watchlist + counts.watched + counts.diary + counts.profile_favorite;
}

/** Whether the account holds anything an import would take from it. */
function holds(warning: ImportWarning): boolean {
  const { rated_films, judgments, anchors, backlog_films, watch_events } = warning;
  return rated_films + judgments + anchors + backlog_films + watch_events > 0;
}

function destroyed(warning: ImportWarning): string {
  const parts = [
    plural(warning.rated_films, "rating"),
    plural(warning.judgments, "recorded answer"),
    plural(warning.anchors, "anchor"),
    plural(warning.backlog_films, "backlog film"),
    plural(warning.watch_events, "logged watch", "logged watches"),
  ].filter((part) => part !== null);
  return parts.length > 0 ? parts.join(", ") : "everything this account holds";
}

/** What a row was, in the owner's words rather than the pipeline's. */
function describe(row: ImportUnmatchedRow): string {
  if (row.kind === "rating") return row.rating === null ? "A rating" : `You rated it ${row.rating.toFixed(1)}`;
  if (row.kind === "watchlist") return "On your watchlist";
  if (row.kind === "watched") return "You marked it watched";
  if (row.kind === "diary") return "A diary entry";
  return "One of your favourites";
}

function plural(count: number, one: string, many?: string): string | null {
  if (count === 0) return null;
  return `${count} ${count === 1 ? one : (many ?? `${one}s`)}`;
}
