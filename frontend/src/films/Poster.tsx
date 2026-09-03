import { posterUrl, type PosterSize } from "./tmdb";

/**
 * A film's poster, hotlinked from TMDB, falling back to a titled placeholder.
 *
 * The size picks which file to fetch, never how big the tile is: every surface sizes its
 * own posters, so the wall can grow one and a list row keep its thumbnail.
 */
export function Poster({
  title,
  path,
  size,
}: {
  title: string;
  path: string | null;
  size: PosterSize;
}) {
  const url = posterUrl(path, size);
  if (url === null) {
    return (
      <div className="poster poster-missing" aria-hidden="true">
        <span>{title}</span>
      </div>
    );
  }
  // The alt text is empty on purpose: the title is already beside it in the markup,
  // so announcing it twice only slows a screen reader down.
  return <img className="poster" src={url} alt="" loading="lazy" />;
}
