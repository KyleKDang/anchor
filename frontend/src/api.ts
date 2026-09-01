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
}

export interface BacklogFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  genres: string[];
  added_at: string;
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
  /** Every genre and decade the whole backlog offers, so a filter never empties its own menu. */
  genres: string[];
  decades: number[];
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
  markWatched: (tmdbId: number) => request<FilmDetail>("POST", `/api/films/${tmdbId}/watched`),
  backlog: (filters: BacklogFilters = {}) => request<Backlog>("GET", `/api/watchlist/backlog${query(filters)}`),
};

function query(filters: BacklogFilters): string {
  const params = new URLSearchParams();
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.genre) params.set("genre", filters.genre);
  if (filters.decade) params.set("decade", String(filters.decade));
  const search = params.toString();
  return search ? `?${search}` : "";
}
