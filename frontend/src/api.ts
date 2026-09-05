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

/**
 * The four answers a comparison offers; `a` and `b` are the two films as shown.
 *
 * `a` is usually the film being placed, and deliberately not always: a quiet drift check
 * rides in the same shape, about two films the owner is not placing at all. Answers name
 * the pair they were shown rather than an opponent, so this client never has to know
 * which kind of question it just rendered - and so it cannot leak the difference.
 */
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

/** The one answer a criteria card takes. Skip is absent: not answering is the default. */
export type CriteriaVerdict = "a" | "b" | "tied";

/**
 * The optional bonus question after a placement: "Which had the better ___?"
 *
 * The wording is a fixed template and `quality` is the only thing that varies, drawn
 * from the account's quality list. Nothing here is generated and there is no free-form
 * question: the intelligence is entirely in which pair and which quality were picked.
 */
export interface CriteriaCard {
  id: string;
  quality: string;
  film_a: FilmCard;
  film_b: FilmCard;
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
  /** How many films are still settling, on the done screen of a settle and nowhere else. */
  settle_another: number | null;
  /** The bonus question this landing earned, and usually null. Never blocking. */
  criteria: CriteriaCard | null;
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
  /** An open drift flag the owner can see: this position is doubted. */
  flagged: boolean;
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
  /** The compact strip at the top: every film carrying a flag the owner can see. */
  needs_attention: FilmCard[];
  rate_later: FilmCard[];
  /** The settling strip's count: films whose position is still a placeholder.
   *
   * Anchors are excluded, because the strip's button will not offer one. Zero means the
   * strip renders nothing at all - presence, not a permanent slot - and this is the only
   * count of it the app ever shows, bar the way onward from a settle just finished. */
  settling: number;
}

export interface RatedFilters {
  sort?: RatedSort;
  bandMin?: number | null;
  bandMax?: number | null;
  genre?: string | null;
  decade?: number | null;
  flagged?: boolean;
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

/**
 * The film has never been placed, so the comparisons that place it decide the band.
 *
 * The fresh account's whole bootstrap: designating both places the film and erects the
 * first dividers. The intent is still only an intent, and answers that land it elsewhere
 * cancel it.
 */
export interface PlacementNeeded {
  outcome: "placement";
  band: number;
  film: FilmCard;
}

export type Designation = Designated | ReplacementNeeded | PlacementNeeded;

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
  /** The open drift flag and its resolution options, where the owner has one to see. */
  drift: DriftFlag | null;
  /** The still-feel-the-same question the last rewatch left open. */
  rewatch: RewatchPrompt | null;
  /** The position is a placeholder, so the page offers to settle it rather than re-place it. */
  provisional: boolean;
}

/** One judgment that contradicts where the film sits, in the owner's own terms. */
export interface DriftJudgment {
  opponent: FilmCard;
  /** The owner put the opponent above this film, against where the two now sit. */
  opponent_won: boolean;
  tied: boolean;
  answered_at: string;
}

/**
 * An open drift flag the owner can see, and what it stands on.
 *
 * Three ways out, one of which is doing nothing: re-place it, keep the position, or not
 * now. Dragging to a slot is deliberately not among them - every move goes through
 * comparisons - and nothing here is urgent, because the film is already benched.
 */
export interface DriftFlag {
  judgments: DriftJudgment[];
  /** A re-placement is already running for this film, so the page resumes it. */
  re_placing: boolean;
  /** Re-placing would risk this film's anchor status, so the offer says so upfront. */
  anchor_warning: boolean;
}

/** How the owner settled one implicated opponent when keeping the position. */
export interface KeepOpponent {
  opponent_tmdb_id: number;
  resolution: "noise" | "re_point";
}

/** The still-feel-the-same offer, open until it is answered and never chased. */
export interface RewatchPrompt {
  watched_at: string;
}

export type RewatchAnswer = "confirmed" | "changed" | "skip";

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

/**
 * How often the bonus criteria question is offered. `adaptive` is the default and reads
 * the owner's engagement; `off` is complete, not merely quieter.
 */
export type CriteriaFrequency = "adaptive" | "often" | "sometimes" | "rarely" | "off";

/**
 * The owner-readable description of their taste, as the Profile screen shows it.
 *
 * `generated_at` is the whole of what the owner is told about the regeneration: one
 * ambient last-updated line, and nothing about what triggered it or whether another is
 * coming. The engine never narrates its background work.
 */
export interface Prose {
  text: string;
  version: number;
  generated_at: string;
}

/** The Profile screen's engine section. `stages` omits cold: every account is already there. */
export interface Profile {
  readiness: Readiness;
  evidence: Evidence;
  stages: Stage[];
  criteria_frequency: CriteriaFrequency;
  /** Null until the first regeneration lands, which an account has to earn. */
  prose: Prose | null;
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

/**
 * One film whose Letterboxd value is out of date, said as old → new.
 *
 * `synced` is what Letterboxd holds as far as Anchor knows, and is null for a film it
 * never saw; `band` is what Anchor holds now, which is the value to type over there.
 */
export interface SyncFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  synced: number | null;
  band: number;
}

/**
 * The sync list, derived rather than stored: the gap between the two rating sets.
 *
 * The two sections are different errands - an edit and a new entry - so they are listed
 * apart. `count` is both, because the ambient count is a count of the work.
 */
export interface SyncList {
  changed: SyncFilm[];
  never_recorded: SyncFilm[];
  count: number;
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

/** Which fill the shared warmup skeleton is running. Derived, never chosen. */
export type WarmupFill = "imported" | "fresh";

/** Where one prompt stands. Skipped and done are both terminal, and both fine. */
export type PromptState = "todo" | "done" | "skipped";

/** A phase that can be skipped as a whole; a band names one designation prompt. */
export type WarmupMark = "anchors" | "evidence" | "backlog";

export interface AnchorPrompt {
  band: number;
  state: PromptState;
  /** The anchor, once one is designated. Its presence is what makes the prompt done. */
  film: FilmCard | null;
  /** Ranked suggestions on the import fill; empty on the fresh fill, which searches. */
  candidates: FilmCard[];
}

export interface AnchorPhase {
  state: PromptState;
  /** The five whole stars, in ease-of-recall order: 5, 1, 3, 4, 2. */
  prompts: AnchorPrompt[];
  /** The five half stars, offered after the whole stars and never before them. */
  continuation: AnchorPrompt[];
  /** Offer the popular/top-rated grid as the explicit "need inspiration?" fallback. */
  browse: boolean;
}

export interface EvidencePhase {
  state: PromptState;
  kind: "comparisons" | "placements";
  answered: number;
  /** Advisory: where the phase stops asking, never a bar the owner has to clear. */
  target: number;
}

export interface BacklogPhase {
  state: PromptState;
  films: number;
  /** Backlog films the import's watchlist put there before the owner arrived. */
  seeded: number;
}

/** The whole warmup, read fresh on every call. Everything but the skips is derived. */
export interface Warmup {
  fill: WarmupFill;
  /** Show the entry fork: the owner has not answered it either way yet. */
  fork: boolean;
  dismissed: boolean;
  anchors: AnchorPhase;
  evidence: EvidencePhase;
  backlog: BacklogPhase;
  readiness: Readiness;
}

/** One warmup comparison: two films the import seeded into the same tie-group. */
export interface WarmupComparison {
  done: false;
  a: FilmCard;
  b: FilmCard;
  answered: number;
  target: number;
  /** The readiness this very answer crossed into, if it crossed one (ADR 0011). */
  unlocked: Readiness | null;
}

export interface WarmupEvidenceDone {
  done: true;
  answered: number;
  target: number;
  unlocked: Readiness | null;
}

export type WarmupStep = WarmupComparison | WarmupEvidenceDone;

/** The two grids TMDB offers as a list rather than an answer to a question. */
export type Browse = "popular" | "top_rated";

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

/** The next film a settling sitting should hand over, and how much is left after it. */
export interface NextFilm {
  /** Null when nothing is left, which is how a sitting ends: it runs out. */
  film: FilmCard | null;
  /** Films still settling, this one included, minus whatever the sitting has passed. */
  remaining: number;
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
  browseFilms: (kind: Browse) =>
    request<{ results: SearchResult[] }>("GET", `/api/films/browse?kind=${kind}`),
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
  answerPlacement: (tmdbId: number, aTmdbId: number, bTmdbId: number, verdict: Verdict) =>
    request<PlacementStep>("POST", `/api/placements/${tmdbId}/answers`, {
      a_tmdb_id: aTmdbId,
      b_tmdb_id: bTmdbId,
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
  /**
   * Pick the next film of a settling sitting, naming what the sitting has been through.
   *
   * The list is the whole of a sitting's state, held by the screen rather than the
   * server: a sitting is a sitting and not a record, so nothing about it outlives the tab.
   */
  nextSettling: (offered: number[]) =>
    request<NextFilm>("POST", "/api/settling/next", { offered }),
  /** "Not this one": decline the offered film, taking back the ask that opened it. */
  passOnSettling: (tmdbId: number) => request<void>("POST", `/api/settling/${tmdbId}/pass`),
  rated: (filters: RatedFilters = {}) => request<Rated>("GET", `/api/rated${ratedQuery(filters)}`),
  anchors: () => request<Anchors>("GET", "/api/anchors"),
  designate: (band: number, tmdbId: number) =>
    request<Designation>("POST", `/api/anchors/${band}`, { tmdb_id: tmdbId }),
  retireAnchor: (band: number) => request<void>("DELETE", `/api/anchors/${band}`),
  rePlaceDrift: (tmdbId: number) => request<void>("POST", `/api/drift/${tmdbId}/re-place`),
  /** The owner asking outright: "settle it now" on a provisional film, "re-place" on a settled one. */
  askToRePlace: (tmdbId: number) =>
    request<void>("POST", `/api/placements/${tmdbId}/re-place`),
  keepPosition: (tmdbId: number, opponents: KeepOpponent[]) =>
    request<void>("POST", `/api/drift/${tmdbId}/keep`, { opponents }),
  logRewatch: (tmdbId: number) => request<FilmDetail>("POST", `/api/films/${tmdbId}/watched`, {}),
  answerRewatch: (tmdbId: number, answer: RewatchAnswer) =>
    request<void>("POST", `/api/rewatches/${tmdbId}`, { answer }),
  profile: () => request<Profile>("GET", "/api/profile"),
  answerCriteria: (offerId: string, verdict: CriteriaVerdict) =>
    request<void>("POST", `/api/criteria/${offerId}`, { verdict }),
  setCriteriaFrequency: (frequency: CriteriaFrequency) =>
    request<{ frequency: CriteriaFrequency }>("PUT", "/api/profile/criteria", { frequency }),
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

  syncList: () => request<SyncList>("GET", "/api/sync"),
  markSynced: (tmdbId: number) => request<void>("POST", `/api/sync/${tmdbId}`),
  markAllSynced: () => request<void>("POST", "/api/sync/all"),

  warmup: () => request<Warmup>("GET", "/api/warmup"),
  enterWarmup: () => request<Warmup>("POST", "/api/warmup/enter"),
  skipWarmup: (mark: WarmupMark, band?: number) =>
    request<Warmup>("POST", "/api/warmup/skip", { mark, band: band ?? null }),
  dismissWarmup: () => request<Warmup>("POST", "/api/warmup/dismiss"),
  warmupComparison: () => request<WarmupStep>("GET", "/api/warmup/comparison"),
  answerWarmupComparison: (aTmdbId: number, bTmdbId: number, verdict: Verdict) =>
    request<WarmupStep>("POST", "/api/warmup/comparison", {
      a_tmdb_id: aTmdbId,
      b_tmdb_id: bTmdbId,
      verdict,
    }),
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
  if (filters.flagged) params.set("flagged", "true");
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
