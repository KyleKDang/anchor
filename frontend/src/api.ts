/** The JSON API client: same-origin, cookie-carried session, one error shape. */

export interface Account {
  id: string;
  email: string;
  verified: boolean;
  /** The shared read-only demo account, whose wall has no edit mode. */
  demo: boolean;
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

/** The one answer a criteria card takes. Skip is absent: not answering is the default. */
export type CriteriaVerdict = "a" | "b" | "tied";

/**
 * One criteria question: "Which had the better ___?"
 *
 * The wording is a fixed template and `quality` is the only thing that varies, drawn
 * from the account's quality list. Nothing here is generated and there is no free-form
 * question: the intelligence is entirely in which pair and which quality were picked.
 * The same card serves both homes: the run on the done screen and the session from a
 * film's page.
 */
export interface CriteriaCard {
  id: string;
  quality: string;
  film_a: FilmCard;
  film_b: FilmCard;
}

/**
 * What every call that can put a card in front of the owner hands back: the card, or
 * null when the home is over - nothing unasked remains, or the run's frequency was
 * switched off. One shape for a session's opening and for what follows an answer.
 */
export interface CriteriaDealt {
  card: CriteriaCard | null;
}

/** The films immediately above and below a landed film, inside its own band. */
export interface Neighbours {
  above: FilmCard | null;
  below: FilmCard | null;
}

/** One band of the picker: its value, and the references the owner picks against. */
export interface PickerBand {
  band: number;
  /** A handful of the band's anchors, most recently marked first. */
  pool: FilmCard[];
  /** The whole pool's size, so a row can say how many more it stands for. */
  pool_total: number;
}

/** The band picker: the film being rated, and the ten bands to put it in. */
export interface Picker {
  film: FilmCard;
  bands: PickerBand[];
  /** The film's band on a re-rate, marked on the row; null when it is not rated yet. */
  current_band: number | null;
  current_rank: number | null;
}

/** A comparison's four answers, as the owner meets them on screen. */
export type ComparisonAnswer = "better" | "worse" | "same" | "skip";

/** The film a comparison sets the subject against, and the band it stands for. */
export interface PickerOpponent {
  film: FilmCard;
  /** What the question is about: the band, never this film's own worth. */
  band: number;
  /** An anchor of that band, or the stand-in a band with no anchor left is shown by. */
  anchor: boolean;
}

/** The boundary question: the two seam films, and which the film is closer to. */
export interface PickerBoundary {
  upper: FilmCard;
  upper_band: number;
  lower: FilmCard;
  lower_band: number;
}

/**
 * Where a narrowing stands. Exactly one of the four is set: a comparison to ask, the
 * boundary question, the bands to hand the owner when nothing is left to ask, or the
 * band the answers settled on.
 */
export interface NarrowStep {
  /** The bands still in the range, best first. */
  bands: number[];
  question: PickerOpponent | null;
  boundary: PickerBoundary | null;
  choose: boolean;
  band: number | null;
}

/** What a pick carries when it ends a range rather than being made outright. */
export interface Narrowed {
  /** The range the owner selected. */
  bands: number[];
  /** Every answer given, in order: what the landing is clipped to. */
  answered: ComparisonAnswer[];
  /** The boundary film the owner judged it closer to, and null for every other pick. */
  closer?: number | null;
}

/** The done screen: where the film landed, and the two ways on from it. */
export interface Landed {
  film: FilmCard;
  band: number;
  rank: number;
  /** How many films the band holds, so the rank reads as "3 of 41". */
  band_size: number;
  /** The film carries an anchor mark, which a cross-band re-rate has just retired. */
  anchor: boolean;
  neighbours: Neighbours;
  /** What this very landing unlocked, and empty on every other one. */
  unlocked: Unlock[];
  /** The account has no anchors at all: the one line saying what marking one does. */
  anchor_nudge: boolean;
  /** The first card of the run this landing earned, and usually null. Never blocking. */
  criteria: CriteriaCard | null;
}

/** Position is the wall itself; every other sort drops the band rows. */
export type RatedSort = "position" | "rated" | "watched" | "title" | "year";

export interface RatedFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  genres: string[];
  /** The film's rating: the band the owner put it in. */
  band: number;
  /** Position within the band, 1 the best. Stamped on the poster. */
  rank: number;
  /** The owner has marked this film as one they are certain of. */
  anchor: boolean;
}

/** One band row of the wall: its films in rank order, and what its header says. */
export interface BandRow {
  band: number;
  films: RatedFilm[];
  /** The count of the band's anchors - the whole band's, not the filtered view's. */
  anchors: number;
}

/** Where a film sits after a move; `anchor` is false once a cross-band move retired it. */
export interface Moved {
  tmdb_id: number;
  band: number;
  rank: number;
  anchor: boolean;
  /** What this very drop unlocked, and empty on every other one. */
  unlocked: Unlock[];
}

export interface Rated {
  sort: RatedSort;
  /** The wall, for the position sort; null for every other. A band with nothing is absent. */
  rows: BandRow[] | null;
  /** The flat list, for every sort but position; null for that one. */
  films: RatedFilm[] | null;
  bands: number[];
  genres: string[];
  decades: number[];
  /** How many films each band holds, filter or no filter, keyed by the band as a string. */
  sizes: Record<string, number>;
  /** No anchor exists yet: the one line saying what marking one does. */
  anchor_nudge: boolean;
  /** The warmup's look-over-the-wall step is unanswered: explain dragging and marking. */
  wall_hint: boolean;
  rate_later: FilmCard[];
}

export interface RatedFilters {
  sort?: RatedSort;
  bandMin?: number | null;
  bandMax?: number | null;
  genre?: string | null;
  decade?: number | null;
  anchorsOnly?: boolean;
}

/** One band's anchor pool, most recently marked first. */
export interface BandPool {
  band: number;
  films: FilmCard[];
}

/** Every band's pool, best band first. A band with no anchors is still listed. */
export interface Anchors {
  bands: BandPool[];
}

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
  /** The film's band, on a rated film. Null on every other, and never a prediction. */
  rating: number | null;
  /** The owner has marked this film an anchor. */
  anchor: boolean;
  /** The rate-later seat; meaningful only while the film is watched-unrated. */
  rate_later: boolean;
  /** Where the film sits inside its band, 1 the best. Null on an unrated film. */
  rank: number | null;
  /** How many films the band holds, so the rank reads as "3 of 41". */
  band_size: number | null;
  /** The films immediately above and below it in its band. Null on an unrated film. */
  neighbours: Neighbours | null;
  /** The still-feel-the-same question the last rewatch left open. */
  rewatch: RewatchPrompt | null;
  /** The film's comparison-log entries, newest first. Empty on an unrated film. */
  judgments: Judgment[];
}

/** The log's three row types (rating-system.md, "The comparison log"). */
export type JudgmentKind = "band_comparison" | "band_pick" | "criteria";

/**
 * One of the film's own comparison-log entries, as the page shows it back.
 *
 * No status and no flag: an entry the ordering has since been moved past is shown
 * exactly as it was made, and the reader compares it with the band and rank above it.
 */
export interface Judgment {
  kind: JudgmentKind;
  /** The film this judgment set the subject against; null on a plain band pick. */
  other: FilmCard | null;
  /** Which film won a comparison. Null on a band pick, whose answer is the band. */
  verdict: "a" | "b" | "tied" | "skip" | null;
  /** The band a pick chose. Null on every comparison. */
  band: number | null;
  /** The quality a criteria answer was about. Null on every other kind. */
  quality: string | null;
  created_at: string;
}

/** The still-feel-the-same offer, open until it is answered and never chased. */
export interface RewatchPrompt {
  watched_at: string;
}

export type RewatchAnswer = "confirmed" | "changed" | "skip";

/** What the account's evidence currently supports. There is no time component. */
export type Readiness = "cold" | "forming" | "ready";

/** What a bar measures. Two dimensions, because there are only two (ADR 0013). */
export type Dimension = "rated_films" | "bands_spanned";

export interface Evidence {
  rated_films: number;
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

/**
 * What a correction rules out mechanically, where it rules anything out at all.
 *
 * Most corrections are about the shape of the writing and carry none. The ones that are
 * about a fact with edges - "stop suggesting me horror" - become a rule the discovery
 * prefilter enforces by dropping films, rather than an instruction the next regeneration
 * is asked to write around.
 */
export interface Footprint {
  genre: string | null;
  language: string | null;
}

/** A claim in the prose the owner thumbed down, kept as a row rather than as an edit. */
export interface Correction {
  id: string;
  claim: string;
  /** Null on the common correction, which is about the writing and rules nothing out. */
  excludes: Footprint | null;
  created_at: string;
}

/** One language a footprint may name: the code that is stored, and the name shown. */
export interface FootprintLanguage {
  code: string;
  name: string;
}

/** Everything a footprint is allowed to name, so it is chosen rather than typed. */
export interface Vocabulary {
  genres: string[];
  languages: FootprintLanguage[];
}

/** The Profile screen's engine section. `stages` omits cold: every account is already there. */
export interface Profile {
  readiness: Readiness;
  evidence: Evidence;
  stages: Stage[];
  criteria_frequency: CriteriaFrequency;
  /** Null until the first regeneration lands, which an account has to earn. */
  prose: Prose | null;
  /** What the owner has corrected and not taken back, shown beside the prose it corrects. */
  corrections: Correction[];
}

/** Where a quality on the account's list came from. Downstream the two are identical. */
export type QualityOrigin = "built_in" | "custom";

/**
 * One row of the quality picker.
 *
 * `checked` is what the checkbox shows and the server decides it: the owner's own
 * selection once they have answered, and Anchor's guess before that. `suggested` is only
 * there so the screen can say the ticks are a guess rather than a memory.
 */
export interface Quality {
  id: string;
  name: string;
  origin: QualityOrigin;
  checked: boolean;
  suggested: boolean;
}

/** The picker. `answered` is false while the ticks are Anchor's guess rather than an answer. */
export interface Picker {
  answered: boolean;
  qualities: Quality[];
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

/** The two readiness unlocks, which are the only things that ever get a nav dot. */
export type Unlock = "discovery" | "watchlist";

/** The nav's one-time dots. Reserved for the readiness unlocks, and nothing else ever. */
export interface Unlocks {
  /** Discovery unlocks a whole readiness state earlier, so this is the first to fire. */
  discovery: boolean;
  watchlist: boolean;
}

/**
 * One card on the discovery shelf.
 *
 * There is deliberately no fit, no bucket, no score and no rank: position is the entire
 * public statement (ADR 0005), and the pitch is the only thing the engine ever says out
 * loud about why a film is here.
 */
export interface Suggestion {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  genres: string[];
  directors: string[];
  /** The plot summary, shown behind the spoiler toggle every surface puts it behind. */
  overview: string;
  /** "Because you loved X and Y - ...", precomputed and visible by default. */
  pitch: string;
  /**
   * New since the owner's last visit.
   *
   * Freshness, never fit: it says when the card arrived and nothing about how good a
   * match it is, so ADR 0005 is untouched. It marks the card and stops there - positions
   * are not reordered around it and nothing at nav level ever counts it.
   */
  fresh: boolean;
}

/** One film on the reviewable dismissed list, behind the Discovery overflow. */
export interface DismissedFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
}

/** What one owner action on a card leaves behind: the shelf, and any invite it earned. */
export interface Acted {
  /** The whole shelf, gap closed and slot backfilled, rather than a diff to apply. */
  films: Suggestion[];
  /**
   * Offer to rate the film now. Seen-it only, and an invite rather than a step: skipping
   * it costs nothing, because the film is already waiting in the rate-later queue.
   */
  place_now: boolean;
}

/** The Discovery screen: the shelf, or the honest explanation of why there is not one. */
export interface Feed {
  readiness: Readiness;
  unlocked: boolean;
  /** Why the feed is not live yet. Null once it is. */
  progress: TierProgress | null;
  /** Up to about twenty, and fewer whenever the pipeline is thin. Never padded. */
  films: Suggestion[];
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
  /** What the import unlocked and the owner has not yet been to see. */
  unlocked: Unlock[];
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
  /** Every row of the comparison log: the picks and the criteria answers alike. */
  judgments: number;
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

/** A phase that can be skipped as a whole; a band names one anchor prompt. */
export type WarmupMark = "anchors" | "rating" | "wall" | "backlog";

export interface AnchorPrompt {
  band: number;
  state: PromptState;
  /** The band's anchor pool. Any number may be marked, so one makes the prompt done. */
  marked: FilmCard[];
  /** The band's own films, best-remembered first, minus what is already marked.
   *
   * A marked band keeps its list: any number may be marked, and the second one is in
   * the same place the first came from. Empty once the band is skipped. */
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

/**
 * The fresh fill's middle step: "rate ~5 films you have seen", as normal ratings.
 *
 * Absent on the import fill, whose middle step is {@link WallPhase} instead.
 */
export interface RatingPhase {
  state: PromptState;
  rated: number;
  /** Advisory: where the phase stops asking, never a bar the owner has to clear. */
  target: number;
}

/**
 * The import fill's middle step: the wall the export just built, in edit mode.
 *
 * Absent on the fresh fill, which has nothing yet to look over.
 */
export interface WallPhase {
  state: PromptState;
  /** Films the owner has moved, ever. */
  moved: number;
  /** Advisory: where the step stops asking. The wall was already theirs to edit.
   *
   * The step's one-time explanation is not here: it explains dragging, so it rides the
   * screen the step sends the owner to, as the Rated payload's `wall_hint`. */
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
  /** The fresh fill's middle step, and null on the import fill, which has the wall. */
  rating: RatingPhase | null;
  /** The import fill's middle step, and null on the fresh fill, which has nothing yet. */
  wall: WallPhase | null;
  backlog: BacklogPhase;
  readiness: Readiness;
}

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

/** The server's one refusal for a write on the demo account, and the only code we key on. */
export const READ_ONLY_DEMO = "demo_read_only";

/**
 * Thrown in place of a write the demo account may not perform.
 *
 * It is not a failure and carries nothing to show: by the time it is thrown the pitch is
 * already on screen, so every surface that renders an error renders nothing for this one.
 */
export class ReadOnlyDemo extends Error {
  constructor() {
    super("read-only demo");
  }
}

/** What to show a person for a failed call, whatever was thrown. */
export function messageOf(error: unknown): string {
  if (error instanceof ReadOnlyDemo) return "";
  return error instanceof ApiError ? error.message : "Something went wrong.";
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let readOnly = false;
let pitch: (() => void) | null = null;

/**
 * Tell the client whether this session is the demo, from wherever the account is known.
 *
 * Module state rather than a hook, because this is the one place every write in the app
 * passes through and the intercept has to sit *in front of* the request rather than
 * around each control. Every write control stays on screen and stays pressable - the
 * verbs are part of what the demo is showing - and pressing one lands here
 * (demo-account.md). The exception is the wall's edit mode, which is absent rather than
 * intercepted, and reads the same flag off the account it already holds.
 */
export function readOnlySession(demo: boolean): void {
  readOnly = demo;
}

/** Register what a refused write brings up. The pitch component owns this. */
export function onRefusedWrite(handler: (() => void) | null): void {
  pitch = handler;
}

function refuseWrite(): ReadOnlyDemo {
  pitch?.();
  return new ReadOnlyDemo();
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (readOnly && WRITE_METHODS.has(method)) throw refuseWrite();
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw asRefusal(toApiError(response.status, payload));
  return payload as T;
}

/**
 * The server's refusal, folded into the same interception the client-side gate performs.
 *
 * The backend is the source of truth and this is what says so: a session the client has
 * not learned is a demo yet - the flag arrives one round trip after the app mounts -
 * still meets the pitch rather than a raw error.
 */
function asRefusal(error: ApiError): Error {
  if (error.code !== READ_ONLY_DEMO) return error;
  readOnly = true;
  return refuseWrite();
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
    request<{ results: SearchResult[] }>(
      "GET",
      `/api/films/search?query=${encodeURIComponent(query)}`,
    ),
  browseFilms: (kind: Browse) =>
    request<{ results: SearchResult[] }>("GET", `/api/films/browse?kind=${kind}`),
  film: (tmdbId: number) => request<FilmDetail>("GET", `/api/films/${tmdbId}`),
  addToBacklog: (tmdbId: number) => request<FilmDetail>("POST", `/api/films/${tmdbId}/backlog`),
  removeFromBacklog: (tmdbId: number) => request<void>("DELETE", `/api/films/${tmdbId}/backlog`),
  markWatched: (tmdbId: number, rate: Rate) =>
    request<FilmDetail>("POST", `/api/films/${tmdbId}/watched`, { rate }),
  leaveRateLater: (tmdbId: number) => request<void>("DELETE", `/api/films/${tmdbId}/rate-later`),
  /** Open the band picker on a watched film, or on a rated one to re-rate it. */
  picker: (tmdbId: number) => request<Picker>("GET", `/api/placements/${tmdbId}`),
  /**
   * One step of narrowing a range: hand back the transcript, get the next question.
   *
   * The screen carries the answers rather than the server storing them, so a narrowing
   * has nothing to resume and nothing to clean up: walking away is walking away, and the
   * answers already given are in the log from the moment they were given.
   */
  narrow: (
    tmdbId: number,
    bands: number[],
    answered: ComparisonAnswer[],
    verdict: ComparisonAnswer | null = null,
  ) =>
    request<NarrowStep>("POST", `/api/placements/${tmdbId}/narrow`, { bands, answered, verdict }),
  /** Tap a band, which is the whole of rating a film - or land the range it narrowed to. */
  pickBand: (tmdbId: number, band: number, narrowed?: Narrowed) =>
    request<Landed>("POST", `/api/placements/${tmdbId}/band`, { band, ...narrowed }),
  rated: (filters: RatedFilters = {}) => request<Rated>("GET", `/api/rated${ratedQuery(filters)}`),
  /** Drop a film at a rank in a band on the wall. Every drop saves at once. */
  move: (tmdbId: number, band: number, rank: number) =>
    request<Moved>("POST", `/api/rated/${tmdbId}/move`, { band, rank }),
  anchors: () => request<Anchors>("GET", "/api/anchors"),
  markAnchor: (tmdbId: number) => request<void>("POST", `/api/anchors/${tmdbId}`),
  retireAnchor: (tmdbId: number) => request<void>("DELETE", `/api/anchors/${tmdbId}`),
  logRewatch: (tmdbId: number) => request<FilmDetail>("POST", `/api/films/${tmdbId}/watched`, {}),
  answerRewatch: (tmdbId: number, answer: RewatchAnswer) =>
    request<void>("POST", `/api/rewatches/${tmdbId}`, { answer }),
  profile: () => request<Profile>("GET", "/api/profile"),
  answerCriteria: (offerId: string, verdict: CriteriaVerdict) =>
    request<CriteriaDealt>("POST", `/api/criteria/${offerId}`, { verdict }),
  /** Session only: the next card without an answer. On a run card it ends the run. */
  dismissCriteria: (offerId: string) =>
    request<CriteriaDealt>("POST", `/api/criteria/${offerId}/dismiss`, {}),
  openCriteriaSession: (tmdbId: number) =>
    request<CriteriaDealt>("POST", `/api/criteria/session/${tmdbId}`, {}),
  setCriteriaFrequency: (frequency: CriteriaFrequency) =>
    request<{ frequency: CriteriaFrequency }>("PUT", "/api/profile/criteria", { frequency }),
  qualities: () => request<Picker>("GET", "/api/profile/qualities"),
  /** Answering replaces the whole selection: what is left ticked is the answer. */
  pickQualities: (qualityIds: string[]) =>
    request<Picker>("PUT", "/api/profile/qualities", { quality_ids: qualityIds }),
  addQuality: (name: string) => request<Quality>("POST", "/api/profile/qualities", { name }),
  /** The catalog's own genres and languages, for the correction form to offer. */
  footprintVocabulary: () => request<Vocabulary>("GET", "/api/profile/footprint"),
  /**
   * Thumb a claim down. `excludes` is the footprint where the owner named one, and the
   * server refuses anything outside the vocabulary above, so the two cannot drift apart.
   */
  correctProse: (claim: string, excludes: Footprint | null = null) =>
    request<Correction>("POST", "/api/profile/constraints", {
      claim,
      ...(excludes === null ? {} : { excludes }),
    }),
  liftCorrection: (id: string) => request<void>("DELETE", `/api/profile/constraints/${id}`),
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
  /** Arriving at Discovery, which is what clears its dot. */
  seenDiscovery: () => request<void>("DELETE", "/api/unlocks/discovery"),
  /**
   * The shelf, and the moment the next restock is queued. Arriving at the screen is the
   * session boundary the shelf changes at; the screen reloading after the owner's own
   * action is not one, and says so.
   */
  feed: ({ boundary = true }: { boundary?: boolean } = {}) =>
    request<Feed>("GET", `/api/discovery${boundary ? "" : "?boundary=false"}`),
  /** "I want to watch this": the film joins the backlog, and nothing learns anything. */
  acceptSuggestion: (tmdbId: number) => request<Acted>("POST", `/api/discovery/${tmdbId}/accept`),
  /** "Not interested": suppressed until lifted, and kept on the dismissed list. */
  dismissSuggestion: (tmdbId: number) =>
    request<Acted>("POST", `/api/discovery/${tmdbId}/dismissal`),
  /** "I have already seen this": watched-unrated, with a seat and one skippable offer. */
  seenSuggestion: (tmdbId: number) => request<Acted>("POST", `/api/discovery/${tmdbId}/seen`),
  dismissedSuggestions: () =>
    request<{ films: DismissedFilm[] }>("GET", "/api/discovery/dismissals"),
  liftDismissal: (tmdbId: number) => request<void>("DELETE", `/api/discovery/${tmdbId}/dismissal`),

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
};

/**
 * The export goes up as the raw request body, not as a multipart form.
 *
 * One file and two scalars is not a form, and posting the bytes as themselves lets the
 * server read a stream it can cut off the moment the upload goes over its size cap.
 */
async function uploadExport(file: File, confirm?: string): Promise<ImportState> {
  if (readOnly) throw refuseWrite();
  const params = new URLSearchParams({ name: file.name });
  if (confirm !== undefined) params.set("confirm", confirm);
  const response = await fetch(`/api/import?${params.toString()}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw asRefusal(toApiError(response.status, payload));
  return payload as ImportState;
}

function ratedQuery(filters: RatedFilters): string {
  const params = new URLSearchParams();
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.bandMin != null) params.set("band_min", String(filters.bandMin));
  if (filters.bandMax != null) params.set("band_max", String(filters.bandMax));
  if (filters.genre) params.set("genre", filters.genre);
  if (filters.decade) params.set("decade", String(filters.decade));
  if (filters.anchorsOnly) params.set("anchors_only", "true");
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
