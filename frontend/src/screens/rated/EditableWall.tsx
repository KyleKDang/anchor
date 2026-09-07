import {
  closestCenter,
  DndContext,
  DragOverlay,
  MeasuringStrategy,
  MouseSensor,
  pointerWithin,
  TouchSensor,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import { arrayMove, rectSortingStrategy, SortableContext, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router";

import { api, messageOf, type BandRow, type Rated, type RatedFilm } from "../../api";
import { Band } from "../../films/Band";
import { Poster } from "../../films/Poster";
import { filmPath, releaseYear } from "../../films/tmdb";
import { applyMove, ensureRow, rankAfter, stepTarget, type Step, type Target } from "./moves";

/**
 * The wall in edit mode: every poster drags, within its band and into any other.
 *
 * The wall keeps its grid (visual-design.md, "The wall-versus-rows rule"). The dragged
 * poster lifts on the strong shadow, the cell it would fill opens in the row under the
 * pointer, the anchor toggle sits on each poster where the badge sits, and the band
 * headers stay sticky so a cross-band drag always has its target row's label in view.
 *
 * Every drop saves at once: there is no save button and no draft state, and the visible
 * result is the confirmation (screens-and-flows.md, "Edit mode"). The wall applies each
 * move to what it is showing the moment it happens and re-reads itself once the burst of
 * saves behind it has drained, so the server's answer confirms rather than reveals. A
 * save that fails puts the poster back and says so under it.
 *
 * A poster is also a keyboard control: focused, it moves one rank with the left and
 * right arrows, to its band's ends with Shift (or Home and End), and across bands with
 * up and down. Each step is a move saved at once, the same as a drop.
 */
export function EditableWall({
  rated,
  bands,
  highlighted,
  onMoved,
  onSettled,
}: {
  rated: Rated;
  /** Every band the editor draws, best first: the ten narrowed to the band filter. */
  bands: number[];
  /** The film just landed by the picker, ringed until it is moved. */
  highlighted: number | null;
  /** The highlighted film was moved, so the ring has done its job. */
  onMoved: () => void;
  /** Every queued save has settled: time to re-read the wall. */
  onSettled: () => Promise<void>;
}) {
  const [rows, setRows] = useState<BandRow[]>(() => withBands(rated.rows ?? [], bands));
  const [active, setActive] = useState<RatedFilm | null>(null);
  const [failed, setFailed] = useState<{ tmdb_id: number; message: string } | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const snapshot = useRef<BandRow[] | null>(null);
  const pending = useRef(0);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const refocus = useRef<number | null>(null);

  // The server's answer replaces the local wall only while nothing is still saving: a
  // read that resolved mid-burst would put a poster back where it was three drops ago.
  useEffect(() => {
    if (pending.current === 0) setRows(withBands(rated.rows ?? [], bands));
  }, [rated, bands]);

  // A keyboard move can unmount the focused poster (a cross-band move changes its
  // parent), so focus is put back on the film by id once the rows have re-rendered.
  useEffect(() => {
    if (refocus.current === null) return;
    document.getElementById(handleId(refocus.current))?.focus();
    refocus.current = null;
  }, [rows]);

  /**
   * Queue one save behind the last. Saves run one at a time, in order, because a burst
   * of keyboard steps is a burst of moves whose ranks each assume the one before landed.
   */
  const enqueue = useCallback(
    (film: RatedFilm, work: () => Promise<void>) => {
      pending.current += 1;
      chain.current = chain.current
        .then(work)
        .catch((caught: unknown) => setFailed({ tmdb_id: film.tmdb_id, message: messageOf(caught) }))
        .finally(() => {
          pending.current -= 1;
          if (pending.current === 0) void onSettled();
        });
    },
    [onSettled],
  );

  /** Apply a move to the wall as shown and queue its save. `base` is the wall to apply it to. */
  const move = useCallback(
    (film: RatedFilm, target: Target, base: BandRow[] = rowsRef.current) => {
      setFailed(null);
      setRows(applyMove(base, film, target));
      if (film.tmdb_id === highlighted) onMoved();
      enqueue(film, async () => {
        await api.move(film.tmdb_id, target.band, target.rank);
      });
    },
    [enqueue, highlighted, onMoved],
  );

  const toggleAnchor = useCallback(
    (film: RatedFilm) => {
      setFailed(null);
      setRows(withAnchor(rowsRef.current, film, !film.anchor));
      enqueue(film, async () => {
        if (film.anchor) await api.retireAnchor(film.tmdb_id);
        else await api.markAnchor(film.tmdb_id);
      });
    },
    [enqueue],
  );

  const step = useCallback(
    (film: RatedFilm, which: Step) => {
      const target = stepTarget(film, which, rowsRef.current, bands);
      if (target === null) return;
      refocus.current = film.tmdb_id;
      move(film, target);
    },
    [bands, move],
  );

  // A mouse drag starts after a few pixels, so a click on the poster is still a click; a
  // touch drag starts after a short hold, so a finger on the wall can still scroll it.
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 6 } }),
  );

  function onDragStart({ active }: DragStartEvent) {
    snapshot.current = rowsRef.current;
    setActive(findFilm(rowsRef.current, active.id));
    setFailed(null);
  }

  /** Crossing into another band moves the poster into that row, which is what opens the gap. */
  function onDragOver({ active, over }: DragOverEvent) {
    if (over === null) return;
    const to = bandData(over.data.current);
    if (to === null) return;
    // Everything is read inside the updater: two of these can fire between renders, and
    // a row read outside would move the film out of a row it had already left.
    setRows((current) => {
      const from = bandOf(current, active.id);
      const film = findFilm(current, active.id);
      const target = current.find((row) => row.band === to);
      if (from === null || from === to || film === null || target === undefined) return current;
      const overIndex = target.films.findIndex((one) => one.tmdb_id === over.id);
      const rect = active.rect.current.translated;
      const after =
        overIndex >= 0 && rect !== null
          ? rect.top + rect.height / 2 > over.rect.top + over.rect.height ||
            (rect.top < over.rect.top + over.rect.height &&
              rect.left + rect.width / 2 > over.rect.left + over.rect.width / 2)
          : false;
      const at = overIndex >= 0 ? overIndex + (after ? 1 : 0) : target.films.length;
      return current.map((row) => {
        if (row.band === from) {
          return { ...row, films: row.films.filter((one) => one.tmdb_id !== film.tmdb_id) };
        }
        if (row.band === to) {
          const films = [...row.films];
          films.splice(at, 0, film);
          return { ...row, films };
        }
        return row;
      });
    });
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    const before = snapshot.current ?? rowsRef.current;
    snapshot.current = null;
    setActive(null);
    const film = findFilm(before, active.id);
    const to = over === null ? null : bandData(over.data.current);
    if (film === null || to === null || over === null) {
      setRows(before);
      return;
    }
    const target = rowsRef.current.find((row) => row.band === to);
    if (target === undefined) {
      setRows(before);
      return;
    }
    const ids = target.films.map((one) => one.tmdb_id);
    const activeIndex = ids.indexOf(film.tmdb_id);
    const overIndex = ids.indexOf(Number(over.id));
    const ordered =
      activeIndex >= 0 && overIndex >= 0 ? arrayMove(target.films, activeIndex, overIndex) : target.films;
    const landed = ordered.findIndex((one) => one.tmdb_id === film.tmdb_id);
    const unchanged =
      to === film.band &&
      sameOrder(
        ordered,
        before.find((row) => row.band === film.band)?.films ?? [],
      );
    // The drop is applied to the wall as it was before the drag, not to the wall as the
    // drag left it: the poster already sits in the target row there, and moving it in
    // again would draw it twice.
    if (landed < 0 || unchanged) {
      setRows(before);
      return;
    }
    move(film, { band: to, rank: rankAfter(film, to, ordered[landed - 1] ?? null) }, before);
  }

  function onDragCancel() {
    if (snapshot.current !== null) setRows(snapshot.current);
    snapshot.current = null;
    setActive(null);
  }

  const reducedMotion = useMemo(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={collision}
      // Re-measure every cell while dragging: a poster crossing into another row reflows
      // that row, and collisions read against the cells as they were would put the gap
      // one poster off from where the pointer is.
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
      onDragCancel={onDragCancel}
      accessibility={{ screenReaderInstructions: { draggable: INSTRUCTIONS }, announcements }}
    >
      {rows.map((row) => (
        <EditableBand
          key={row.band}
          row={row}
          activeId={active?.tmdb_id ?? null}
          highlighted={highlighted}
          failed={failed}
          onStep={step}
          onToggleAnchor={toggleAnchor}
        />
      ))}
      <DragOverlay dropAnimation={reducedMotion ? null : undefined}>
        {active !== null && (
          <div className="ordering-film ordering-film-lifted">
            <Poster title={active.title} path={active.poster_path} size="w342" />
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}

/**
 * One band row of the editor. Every band the filter allows is drawn, empty ones too: a
 * band holding nothing is still somewhere a film can be dropped.
 */
function EditableBand({
  row,
  activeId,
  highlighted,
  failed,
  onStep,
  onToggleAnchor,
}: {
  row: BandRow;
  activeId: number | null;
  highlighted: number | null;
  failed: { tmdb_id: number; message: string } | null;
  onStep: (film: RatedFilm, step: Step) => void;
  onToggleAnchor: (film: RatedFilm) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: bandKey(row.band), data: { band: row.band } });
  const ids = row.films.map((film) => film.tmdb_id);

  return (
    <section className="band-group" id={bandDomId(row.band)} aria-label={`${row.band.toFixed(1)} stars`}>
      <header className="band-header">
        <h3>
          <Band band={row.band} />
        </h3>
        {row.anchors > 0 && (
          <span className="muted">
            {row.anchors} {row.anchors === 1 ? "anchor" : "anchors"}
          </span>
        )}
      </header>
      <SortableContext items={ids} strategy={rectSortingStrategy}>
        <ol
          ref={setNodeRef}
          className="ordering ordering-editable"
          data-empty={row.films.length === 0 ? "true" : undefined}
          data-over={isOver ? "true" : undefined}
        >
          {row.films.length === 0 && (
            <li className="ordering-empty muted" aria-hidden="true">
              Drop a film here to make it a {row.band.toFixed(1)}
            </li>
          )}
          {row.films.map((film) => (
            <EditableCell
              key={film.tmdb_id}
              film={film}
              band={row.band}
              lifted={film.tmdb_id === activeId}
              highlighted={film.tmdb_id === highlighted}
              error={failed?.tmdb_id === film.tmdb_id ? failed.message : null}
              onStep={onStep}
              onToggleAnchor={onToggleAnchor}
            />
          ))}
        </ol>
      </SortableContext>
    </section>
  );
}

/**
 * One cell of the editable wall: the poster is the handle, the title is still a link,
 * and the anchor toggle sits where the badge sits.
 *
 * The rank on the poster is the film's real place in its band, exactly as on the
 * read-only wall; a drop renumbers it at once.
 */
function EditableCell({
  film,
  band,
  lifted,
  highlighted,
  error,
  onStep,
  onToggleAnchor,
}: {
  film: RatedFilm;
  band: number;
  lifted: boolean;
  highlighted: boolean;
  error: string | null;
  onStep: (film: RatedFilm, step: Step) => void;
  onToggleAnchor: (film: RatedFilm) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({
    id: film.tmdb_id,
    data: { band },
    // The product's movement timing (visual-design.md); reduced motion drops it globally.
    transition: { duration: 180, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" },
  });

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const step = stepFor(event);
    if (step === null) return;
    event.preventDefault();
    onStep(film, step);
  }

  return (
    <li
      ref={setNodeRef}
      className="ordering-slot"
      id={`film-${film.tmdb_id}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      data-lifted={lifted ? "true" : undefined}
      data-highlighted={highlighted ? "true" : undefined}
    >
      <span className="ordering-rank">{film.rank}</span>
      <div className="ordering-film">
        <button
          type="button"
          id={handleId(film.tmdb_id)}
          className="ordering-handle"
          aria-label={`Move ${film.title}`}
          {...attributes}
          {...listeners}
          onKeyDown={onKeyDown}
        >
          <Poster title={film.title} path={film.poster_path} size="w342" />
        </button>
        <div className="ordering-film-body">
          <Link className="film-title" to={filmPath(film.tmdb_id)}>
            {film.title}
          </Link>
          <span className="film-year muted">{releaseYear(film.year)}</span>
          <button
            type="button"
            className="anchor-toggle"
            aria-pressed={film.anchor}
            aria-label={film.anchor ? `Retire ${film.title} as an anchor` : `Mark ${film.title} as an anchor`}
            title={
              film.anchor
                ? `One of your definitive ${band.toFixed(1)} films`
                : `Mark this as one of your definitive ${band.toFixed(1)} films`
            }
            onClick={() => onToggleAnchor(film)}
          >
            Anchor
          </button>
          {error && (
            <p className="error ordering-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

// --- The keyboard ---

const INSTRUCTIONS =
  "To move a film, focus its poster and press the left or right arrow to move it one rank, " +
  "Shift with an arrow (or Home and End) to move it to the ends of its band, and the up or " +
  "down arrow to move it into the band above or below. Every step is saved at once.";

function stepFor(event: KeyboardEvent<HTMLButtonElement>): Step | null {
  switch (event.key) {
    case "ArrowLeft":
      return event.shiftKey ? "first" : "up";
    case "ArrowRight":
      return event.shiftKey ? "last" : "down";
    case "Home":
      return "first";
    case "End":
      return "last";
    case "ArrowUp":
      return "better";
    case "ArrowDown":
      return "worse";
    default:
      return null;
  }
}

// --- The drag ---

/**
 * What the pointer is over, resolved to a film wherever there is one.
 *
 * The rows are droppables as well as their films, so a drop into an empty row or past
 * the last poster still lands in a band. A hit on a row is turned into the nearest film
 * in it where the row has any, so a pointer in the gap between two posters does not
 * fling the film to the end of the row.
 */
const collision: CollisionDetection = (args) => {
  const rows = args.droppableContainers.filter((one) => isBandKey(one.id));
  const under = pointerWithin({ ...args, droppableContainers: rows });
  const [row] = under.length > 0 ? under : closestCenter({ ...args, droppableContainers: rows });
  if (row === undefined) return [];
  const band = bandData(args.droppableContainers.find((one) => one.id === row.id)?.data.current);
  const films = args.droppableContainers.filter(
    (one) => !isBandKey(one.id) && bandData(one.data.current) === band,
  );
  return films.length > 0 ? closestCenter({ ...args, droppableContainers: films }) : [row];
};

const announcements: Announcements = {
  onDragStart: () => "Picked up the film.",
  onDragOver: ({ over }) =>
    over === null ? "No longer over a band." : `Over the ${labelOf(over.data.current)} band.`,
  onDragEnd: ({ over }) =>
    over === null
      ? "Dropped outside the wall; nothing moved."
      : `Dropped into the ${labelOf(over.data.current)} band.`,
  onDragCancel: () => "Move cancelled.",
};

function labelOf(data: unknown): string {
  const band = bandData(data);
  return band === null ? "" : band.toFixed(1);
}

// --- Bookkeeping ---

function bandKey(band: number): string {
  return `band:${band}`;
}

function isBandKey(id: UniqueIdentifier): boolean {
  return typeof id === "string" && id.startsWith("band:");
}

function bandDomId(band: number): string {
  return `band-${String(band).replace(".", "-")}`;
}

function handleId(tmdbId: number): string {
  return `move-${tmdbId}`;
}

function bandData(data: unknown): number | null {
  if (typeof data !== "object" || data === null) return null;
  const band = (data as { band?: unknown }).band;
  return typeof band === "number" ? band : null;
}

function findFilm(rows: BandRow[], id: UniqueIdentifier): RatedFilm | null {
  for (const row of rows) {
    const film = row.films.find((one) => one.tmdb_id === id);
    if (film) return film;
  }
  return null;
}

/** The band a film is currently drawn in, which during a drag may not be its own. */
function bandOf(rows: BandRow[], id: UniqueIdentifier): number | null {
  return rows.find((row) => row.films.some((one) => one.tmdb_id === id))?.band ?? null;
}

function sameOrder(a: RatedFilm[], b: RatedFilm[]): boolean {
  return a.length === b.length && a.every((film, index) => film.tmdb_id === b[index]?.tmdb_id);
}

function withBands(rows: BandRow[], bands: number[]): BandRow[] {
  return bands.reduce(ensureRow, rows).filter((row) => bands.includes(row.band));
}

function withAnchor(rows: BandRow[], film: RatedFilm, anchor: boolean): BandRow[] {
  return rows.map((row) =>
    row.band === film.band
      ? {
          ...row,
          anchors: row.anchors + (anchor ? 1 : -1),
          films: row.films.map((one) => (one.tmdb_id === film.tmdb_id ? { ...one, anchor } : one)),
        }
      : row,
  );
}
