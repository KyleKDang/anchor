/** The JSON API client: same-origin, cookie-carried session, one error shape. */

export interface Account {
  id: string;
  email: string;
  verified: boolean;
}

export interface Credentials {
  email: string;
  password: string;
}

/** A film's one exclusive state in this account; `null` means the owner does not track it. */
export type LifecycleState = "backlog" | "watched_unrated" | "rated";

export interface SearchResult {
  tmdb_id: number;
  title: string;
  year: number | null;
  overview: string;
  poster_path: string | null;
  state: LifecycleState | null;
  /** Only ever a number on a rated film; a prediction never appears here (ADR 0005). */
  rating: number | null;
}

/** A film as the ordering and the placement flow show it.
 *
 * There is deliberately no `rating` and no `state` field. Mid-flow the owner answers on
 * the pure which-is-better instinct, so the value is absent from the payload rather than
 * merely hidden by this client.
 */
export interface FilmCard {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  overview: string;
}

/** Logging a watch is always a choice between rating it now and rating it later. */
export type Rate = "now" | "later";

/** The four answers a comparison offers; `a` is always the film being placed. */
export type Verdict = "a" | "b" | "tied" | "skip";

export interface PlacementQuestion {
  done: false;
  kind: "comparison";
  a: FilmCard;
  b: FilmCard;
  answered: number;
  /** The stars are settled and only the neighbours are open, so bailing out is safe. */
  band_locked: boolean;
}

/** One band the landing could belong to, and the canonical film that stands for it. */
export interface BandOption {
  band: number;
  exemplar: FilmCard | null;
}

/**
 * The landing sits exactly on a divider, so only the owner can say which side.
 *
 * This is the one step of the flow that names ratings, deliberately: the question is
 * about the bands, so hiding them would leave nothing to answer.
 */
export interface BandQuestion {
  done: false;
  kind: "band";
  film: FilmCard;
  /** Two adjacent bands with a canonical film each; otherwise it is a plain band pick. */
  sliver: boolean;
  options: BandOption[];
  answered: number;
}

export interface Neighbours {
  above: FilmCard[];
  tied_with: FilmCard[];
  below: FilmCard[];
}

export interface PlacementLanded {
  done: true;
  kind: "landed";
  film: FilmCard;
  /** 1-based rank of the film's slot, best first. */
  position: number;
  total: number;
  /** Derived from position against the dividers; null while the band structure is thin. */
  rating: number | null;
  band_anchor: boolean;
  /** Trusted less than a fully-compared placement, and settles on its own. */
  provisional: boolean;
  /** Position-only and no anchor exists yet: the line explaining the missing stars. */
  anchor_nudge: boolean;
  /** A designation-mismatch re-placement landed in its band and completed the intent. */
  designated: boolean;
  /** This landing crossed the readiness bar: the one line announcing the ranked tier. */
  unlocked: boolean;
  neighbours: Neighbours;
}

export type PlacementStep = PlacementQuestion | BandQuestion | PlacementLanded;

/** Position is the ordering itself; every other sort drops the band grouping. */
export type RatedSort = "position" | "rated" | "watched" | "title" | "year";

export interface RatedFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  genres: string[];
  /** 1-based rank of the film's slot, best first. */
  position: number;
  /** Derived from position against the dividers; null while its band is undecidable. */
  band: number | null;
  anchor: boolean;
  provisional: boolean;
}

/** One run of the ordering sharing a band, or one run that has no band yet. */
export interface BandGroup {
  band: number | null;
  /** Tie-groups, in order; the films in one slot are the ones judged equal. */
  slots: RatedFilm[][];
}

export interface Rated {
  sort: RatedSort;
  /** The banded ordering, for the position sort; null for every other. */
  groups: BandGroup[] | null;
  /** The flat list, for every sort but position; null for that one. */
  films: RatedFilm[] | null;
  bands: number[];
  genres: string[];
  decades: number[];
  /** No anchor exists yet: the one line explaining where the half-stars have gone. */
  anchor_nudge: boolean;
  rate_later: FilmCard[];
}

export interface RatedFilters {
  sort?: RatedSort;
  bandMin?: number | null;
  bandMax?: number | null;
  genre?: string | null;
  decade?: number | null;
}

export interface BandAnchor {
  band: number;
  film: FilmCard | null;
}

export interface Anchors {
  anchors: BandAnchor[];
  nudge: boolean;
}

/** Designating took, because the film was already where the owner said it was. */
export interface Designated {
  outcome: "designated";
  band: number;
  film: FilmCard;
  /** The anchor this one replaced, which stays exactly where it sits in the ordering. */
  retired: FilmCard | null;
}

/** The film is not in that band, so comparisons - not the intent - get to decide. */
export interface ReplacementNeeded {
  outcome: "re_placement";
  band: number;
  film: FilmCard;
}

export type Designation = Designated | ReplacementNeeded;

/** The ten half-star bands, best first. A fixed vocabulary, never fetched. */
export const BANDS = [5, 4.5, 4, 3.5, 3, 2.5, 2, 1.5, 1, 0.5];

export interface FilmDetail {
  tmdb_id: number;
  title: string;
  year: number | null;
  overview: string;
  poster_path: string | null;
  backdrop_path: string | null;
  runtime: number | null;
  genres: string[];
  directors: string[];
  cast: string[];
  vote_average: number;
  vote_count: number;
  state: LifecycleState | null;
  rating: number | null;
  /** This film is the canonical exemplar of its band. */
  anchor: boolean;
  /** The rate-later seat; meaningful only while the film is watched-unrated. */
  rate_later: boolean;
}

/** What the account's evidence currently supports. There is no time component. */
export type Readiness = "cold" | "forming" | "ready";

/** What a bar measures. The last two are the two halves of the explicit-comparison share. */
export type Dimension =
  | "rated_films"
  | "bands_spanned"
  | "settled_share"
  | "comparisons_per_film";

export interface Evidence {
  rated_films: number;
  explicit_comparisons: number;
  /** Rated films the owner's own comparisons settled, not a seed import or an early bail. */
  settled_films: number;
  settled_share: number;
  comparisons_per_film: number;
  bands_spanned: number;
}

/** One bar a readiness state needs cleared, and where the account stands against it. */
export interface Threshold {
  dimension: Dimension;
  have: number;
  need: number;
}

export interface Stage {
  state: Readiness;
  reached: boolean;
  thresholds: Threshold[];
}

/** The Profile screen's engine section. `stages` omits cold: every account is already there. */
export interface Profile {
  readiness: Readiness;
  evidence: Evidence;
  stages: Stage[];
}

export interface BacklogFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  genres: string[];
  added_at: string;
  /** Barred from the ranked tier until lifted, and still every bit a backlog film. */
  vetoed: boolean;
}

/** Every sort the backlog offers. There is deliberately no engine-score sort (ADR 0005). */
export type BacklogSort = "added" | "title" | "year";

export interface BacklogFilters {
  sort?: BacklogSort;
  genre?: string | null;
  decade?: number | null;
}

export interface Backlog {
  films: BacklogFilm[];
  /** Every genre and decade the listed backlog offers, so a filter never empties its own menu. */
  genres: string[];
  decades: number[];
}

/**
 * A ranked-tier row.
 *
 * There is deliberately no score and no rank number: position is the whole of the
 * engine's statement, and it is carried by the order of the list (ADR 0005).
 */
export interface TierFilm extends BacklogFilm {
  /** The owner put this here, so the row offers to take it back rather than to pin it. */
  pinned: boolean;
}

/** How close the account is to unlocking the tier. Ambient only: one line, one thin bar. */
export interface TierProgress {
  share: number;
  thresholds: Threshold[];
}

/** The Watchlist's top half, and what stands in for it before the unlock. */
export interface Tier {
  readiness: Readiness;
  unlocked: boolean;
  /** Why there is no tier yet. Null once there is one. */
  progress: TierProgress | null;
  /** Strictly ordered: a real "watch these next" statement, pins first. */
  up_next: TierFilm[];
  /** The rest of the top thirty, loosely ordered. */
  pool: TierFilm[];
  vetoed: BacklogFilm[];
}

/** The nav's one-time dots. Reserved for the readiness unlocks, and nothing else ever. */
export interface Unlocks {
  watchlist: boolean;
}

/** Which of the export's five files a row came out of; the rest is discarded unread. */
export type ImportRowKind = "rating" | "watchlist" | "watched" | "diary" | "profile_favorite";

export interface ImportCounts {
  rating: number;
  watchlist: number;
  watched: number;
  diary: number;
  profile_favorite: number;
}

/**
 * The import area's whole reading: what was read, and what is left to resolve.
 *
 * `pending` counts lines, because that is what progress is measured in. The two residue
 * figures count films: one film is a line in ratings.csv, another in watched.csv and
 * another in diary.csv, and the owner answers for it once.
 */
export interface ImportState {
  status: "none" | "matching" | "complete";
  source_name: string | null;
  created_at: string | null;
  counts: ImportCounts;
  pending: number;
  review_pending: number;
  unmatched: number;
}

/** One film a review row might be. The director is often all that tells two apart. */
export interface ImportCandidate {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  directors: string[];
}

export interface ImportReviewRow {
  id: string;
  kind: ImportRowKind;
  name: string;
  year: number | null;
  rating: number | null;
  /** The row carries a boxd.it link, so the per-row Letterboxd rescue can be offered. */
  rescuable: boolean;
  /** Ranked by popularity: the owner is looking for the film they have heard of. */
  candidates: ImportCandidate[];
}

/** A line that found nothing. It affects nothing and waits indefinitely. */
export interface ImportUnmatchedRow {
  id: string;
  kind: ImportRowKind;
  name: string;
  year: number | null;
  rating: number | null;
  rescuable: boolean;
}

export interface ImportBound {
  row_id: string;
  film: FilmCard;
}

/** Concretely what re-importing destroys, counted rather than described. */
export interface ImportWarning {
  rated_films: number;
  comparisons: number;
  anchors: number;
  backlog_films: number;
  watch_events: number;
  confirmation_required: boolean;
  confirmation_phrase: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

/** What to show a person for a failed call, whatever was thrown. */
export function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong.";
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw toApiError(response.status, payload);
  return payload as T;
}

function toApiError(status: number, payload: unknown): ApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    return new ApiError(status, String(payload.error.code), String(payload.error.message));
  }
  return new ApiError(status, "unexpected", `Something went wrong (HTTP ${status}).`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export const api = {
  me: () => request<Account>("GET", "/api/auth/me"),
  signUp: (credentials: Credentials) => request<Account>("POST", "/api/auth/signup", credentials),
  verify: (token: string, password: string) =>
    request<Account>("POST", "/api/auth/verify", { token, password }),
  logIn: (credentials: Credentials) => request<Account>("POST", "/api/auth/login", credentials),
  logOut: () => request<void>("POST", "/api/auth/logout"),
  deleteAccount: (password: string) => request<void>("DELETE", "/api/account", { password }),

  searchFilms: (query: string) =>
    request<{ results: SearchResult[] }>("GET", `/api/films/search?query=${encodeURIComponent(query)}`),
  film: (tmdbId: number) => request<FilmDetail>("GET", `/api/films/${tmdbId}`),
  addToBacklog: (tmdbId: number) => request<FilmDetail>("POST", `/api/films/${tmdbId}/backlog`),
  removeFromBacklog: (tmdbId: number) => request<void>("DELETE", `/api/films/${tmdbId}/backlog`),
  markWatched: (tmdbId: number, rate: Rate) =>
    request<FilmDetail>("POST", `/api/films/${tmdbId}/watched`, { rate }),
  leaveRateLater: (tmdbId: number) =>
    request<void>("DELETE", `/api/films/${tmdbId}/rate-later`),
  beginPlacement: (tmdbId: number, ballpark?: number) =>
    request<PlacementStep>(
      "POST",
      `/api/placements/${tmdbId}${ballpark === undefined ? "" : `?ballpark=${ballpark}`}`,
    ),
  answerPlacement: (tmdbId: number, opponentTmdbId: number, verdict: Verdict) =>
    request<PlacementStep>("POST", `/api/placements/${tmdbId}/answers`, {
      opponent_tmdb_id: opponentTmdbId,
      verdict,
    }),
  answerBand: (tmdbId: number, band: number, exemplarTmdbId: number | null) =>
    request<PlacementStep>("POST", `/api/placements/${tmdbId}/band`, {
      band,
      exemplar_tmdb_id: exemplarTmdbId,
    }),
  bailOut: (tmdbId: number) => request<PlacementStep>("POST", `/api/placements/${tmdbId}/bail`),
  keepComparing: (tmdbId: number) =>
    request<PlacementStep>("POST", `/api/placements/${tmdbId}/keep-comparing`),
  rated: (filters: RatedFilters = {}) => request<Rated>("GET", `/api/rated${ratedQuery(filters)}`),
  anchors: () => request<Anchors>("GET", "/api/anchors"),
  designate: (band: number, tmdbId: number) =>
    request<Designation>("POST", `/api/anchors/${band}`, { tmdb_id: tmdbId }),
  retireAnchor: (band: number) => request<void>("DELETE", `/api/anchors/${band}`),
  profile: () => request<Profile>("GET", "/api/profile"),
  backlog: (filters: BacklogFilters = {}) =>
    request<Backlog>("GET", `/api/watchlist/backlog${backlogQuery(filters)}`),
  /**
   * Reading the tier is what maintains it, and what clears the Watchlist's dot. Arriving
   * at the screen is the session boundary the maintenance runs at; the screen reloading
   * after the owner's own action is not one, and says so.
   */
  tier: ({ boundary = true }: { boundary?: boolean } = {}) =>
    request<Tier>("GET", `/api/watchlist/tier${boundary ? "" : "?boundary=false"}`),
  pin: (tmdbId: number) => request<void>("POST", `/api/watchlist/${tmdbId}/pin`),
  unpin: (tmdbId: number) => request<void>("DELETE", `/api/watchlist/${tmdbId}/pin`),
  veto: (tmdbId: number) => request<void>("POST", `/api/watchlist/${tmdbId}/veto`),
  liftVeto: (tmdbId: number) => request<void>("DELETE", `/api/watchlist/${tmdbId}/veto`),
  notNow: (tmdbId: number) => request<void>("POST", `/api/watchlist/${tmdbId}/not-now`),
  unlocks: () => request<Unlocks>("GET", "/api/unlocks"),

  importState: () => request<ImportState>("GET", "/api/import"),
  importWarning: () => request<ImportWarning>("GET", "/api/import/warning"),
  importReview: () => request<{ rows: ImportReviewRow[] }>("GET", "/api/import/review"),
  importUnmatched: () => request<{ rows: ImportUnmatchedRow[] }>("GET", "/api/import/unmatched"),
  uploadExport: (file: File, confirm?: string) => uploadExport(file, confirm),
  bindImportRow: (rowId: string, tmdbId: number) =>
    request<ImportBound>("POST", `/api/import/rows/${rowId}/film`, { tmdb_id: tmdbId }),
  rescueImportRow: (rowId: string) =>
    request<ImportBound>("POST", `/api/import/rows/${rowId}/letterboxd`),
  dismissImportRow: (rowId: string) => request<void>("DELETE", `/api/import/rows/${rowId}`),
};

/**
 * The export goes up as the raw request body, not as a multipart form.
 *
 * One file and two scalars is not a form, and posting the bytes as themselves lets the
 * server read a stream it can cut off the moment the upload goes over its size cap.
 */
async function uploadExport(file: File, confirm?: string): Promise<ImportState> {
  const params = new URLSearchParams({ name: file.name });
  if (confirm !== undefined) params.set("confirm", confirm);
  const response = await fetch(`/api/import?${params.toString()}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw toApiError(response.status, payload);
  return payload as ImportState;
}

function ratedQuery(filters: RatedFilters): string {
  const params = new URLSearchParams();
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.bandMin != null) params.set("band_min", String(filters.bandMin));
  if (filters.bandMax != null) params.set("band_max", String(filters.bandMax));
  if (filters.genre) params.set("genre", filters.genre);
  if (filters.decade) params.set("decade", String(filters.decade));
  const search = params.toString();
  return search ? `?${search}` : "";
}

function backlogQuery(filters: BacklogFilters): string {
  const params = new URLSearchParams();
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.genre) params.set("genre", filters.genre);
  if (filters.decade) params.set("decade", String(filters.decade));
  const search = params.toString();
  return search ? `?${search}` : "";
}
