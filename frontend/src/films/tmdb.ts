/**
 * Film addressing: TMDB's image CDN, and the in-app routes that lead to one film.
 *
 * Only image paths are ever stored; the browser hotlinks the bytes (ADR 0003).
 */

const IMAGE_BASE = "https://image.tmdb.org/t/p";

export type PosterSize = "w92" | "w154" | "w342";

export function posterUrl(path: string | null, size: PosterSize): string | null {
  return path === null ? null : `${IMAGE_BASE}/${size}${path}`;
}

export function filmPath(tmdbId: number): string {
  return `/films/${tmdbId}`;
}

export function releaseYear(year: number | null): string {
  return year === null ? "Year unknown" : String(year);
}

export function placePath(tmdbId: number): string {
  return `/place/${tmdbId}`;
}
