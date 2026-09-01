import { posterUrl, type PosterSize } from "./tmdb";

/** A film's poster, hotlinked from TMDB, falling back to a titled placeholder. */
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
      <div className={`poster poster-${size} poster-missing`} aria-hidden="true">
        <span>{title}</span>
      </div>
    );
  }
  // The alt text is empty on purpose: the title is already beside it in the markup,
  // so announcing it twice only slows a screen reader down.
  return <img className={`poster poster-${size}`} src={url} alt="" loading="lazy" />;
}
