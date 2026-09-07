import { BANDS, type BandRow, type RatedFilm } from "../../api";

/**
 * The arithmetic of a move, kept apart from the drag so it can be read on its own.
 *
 * The move endpoint takes the rank the film should hold once it has landed, and the wall
 * only ever sees the films a filter lets through. Everything here turns "where the
 * poster was dropped" - an upper neighbour, a keyboard step, an end of a band - into that
 * rank against the ranks the visible films actually hold, so a drop between two visible
 * films lands directly after the upper one whatever is hidden between them
 * (screens-and-flows.md, "Edit mode").
 */

/** Where a film is going: the band, and the rank to hold in it. */
export interface Target {
  band: number;
  rank: number;
}

/**
 * The rank a film takes when it lands directly after `upper` in `band`.
 *
 * With no upper film it goes to the top. From another band it takes the slot below the
 * upper film. Inside its own band the answer depends on which way it is travelling:
 * coming up from below, the upper film keeps its rank and the mover takes the next; coming
 * down from above, the mover's departure lifts the upper film by one, so "directly after
 * it" is the rank the upper film held before the move.
 */
export function rankAfter(mover: RatedFilm, band: number, upper: RatedFilm | null): number {
  if (upper === null) return 1;
  if (mover.band !== band || mover.rank > upper.rank) return upper.rank + 1;
  return upper.rank;
}

/** The keys a selected poster answers to, and where each one sends it. */
export type Step = "up" | "down" | "first" | "last" | "better" | "worse";

/**
 * Where a keyboard step sends a film, or null where there is nowhere to go.
 *
 * A step is one rank on an unfiltered wall. Under a filter it is the visible step - past
 * the neighbour the owner can see - by the same rule a drop uses, because a rank the
 * owner cannot see changing is not a move they made. The ends are the band's real ends,
 * whatever is showing. Across bands, the film lands at the near end of the next band:
 * the bottom of the better one and the top of the worse one, which is where it would
 * have been dropped by a drag one row over.
 */
export function stepTarget(
  film: RatedFilm,
  step: Step,
  rows: BandRow[],
  visibleBands: number[],
): Target | null {
  const row = rows.find((one) => one.band === film.band);
  if (row === undefined) return null;
  const shown = row.films;
  const index = shown.findIndex((one) => one.tmdb_id === film.tmdb_id);
  switch (step) {
    case "up": {
      if (film.rank === 1) return null;
      return { band: film.band, rank: rankAfter(film, film.band, shown[index - 2] ?? null) };
    }
    case "down": {
      if (film.rank === row.size) return null;
      const below = shown[index + 1];
      return { band: film.band, rank: below ? rankAfter(film, film.band, below) : row.size };
    }
    case "first":
      return film.rank === 1 ? null : { band: film.band, rank: 1 };
    case "last":
      return film.rank === row.size ? null : { band: film.band, rank: row.size };
    case "better":
    case "worse": {
      const at = visibleBands.indexOf(film.band);
      const next = visibleBands[step === "better" ? at - 1 : at + 1];
      if (next === undefined) return null;
      const size = rows.find((one) => one.band === next)?.size ?? 0;
      return { band: next, rank: step === "better" ? size + 1 : 1 };
    }
  }
}

/**
 * The wall as it will read once a move has saved, applied to what is showing.
 *
 * The same renumbering the server does, run over the visible films so the wall can show
 * the result at once and the refetch behind the drop confirms rather than reveals it.
 * Inside a band the films the mover passes shift by one; across bands its old band
 * closes up and its new band opens a slot, the anchor mark goes, and the header counts
 * follow. Bands are sorted by rank afterwards, which is what seats the mover.
 */
export function applyMove(rows: BandRow[], mover: RatedFilm, target: Target): BandRow[] {
  const moved: RatedFilm = {
    ...mover,
    band: target.band,
    rank: target.rank,
    anchor: mover.band === target.band && mover.anchor,
  };
  const within = mover.band === target.band;
  return ensureRow(rows, target.band).map((row) => {
    if (row.band === mover.band && within) {
      const films = row.films.map((film) => {
        if (film.tmdb_id === mover.tmdb_id) return moved;
        if (target.rank < mover.rank && film.rank >= target.rank && film.rank < mover.rank) {
          return { ...film, rank: film.rank + 1 };
        }
        if (target.rank > mover.rank && film.rank > mover.rank && film.rank <= target.rank) {
          return { ...film, rank: film.rank - 1 };
        }
        return film;
      });
      return { ...row, films: byRank(films) };
    }
    if (row.band === mover.band) {
      return {
        ...row,
        films: row.films
          .filter((film) => film.tmdb_id !== mover.tmdb_id)
          .map((film) => (film.rank > mover.rank ? { ...film, rank: film.rank - 1 } : film)),
        anchors: row.anchors - (mover.anchor ? 1 : 0),
        size: row.size - 1,
      };
    }
    if (row.band === target.band) {
      const films = row.films.map((film) =>
        film.rank >= target.rank ? { ...film, rank: film.rank + 1 } : film,
      );
      return { ...row, films: byRank([...films, moved]), size: row.size + 1 };
    }
    return row;
  });
}

/** The rows with `band` present, empty if it was not, so a move into it has a row to land in. */
export function ensureRow(rows: BandRow[], band: number): BandRow[] {
  if (rows.some((row) => row.band === band)) return rows;
  return [...rows, { band, films: [], anchors: 0, size: 0 }].sort((a, b) => b.band - a.band);
}

function byRank(films: RatedFilm[]): RatedFilm[] {
  return [...films].sort((a, b) => a.rank - b.rank);
}

/**
 * Every band the editor shows: the ten, narrowed to the band filter, empty ones included.
 *
 * A band holding nothing is still a place a film can be dropped, and the only way to
 * make it hold something, so edit mode draws it rather than leaving it out the way the
 * read-only wall does.
 */
export function editableBands(bandMax: number | null, bandMin: number | null): number[] {
  return BANDS.filter(
    (band) => (bandMax === null || band <= bandMax) && (bandMin === null || band >= bandMin),
  );
}
