/** Anchor's mark: an anchor reduced to ring, stock, shank, and flukes, the ring in the rating
    amber and the lines in the wordmark's ink. Hidden from assistive tech, since the wordmark's
    name is the word "Anchor" alone. */
export function Mark() {
  // The same drawing as public/favicon.svg; a change to it is made in both.
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="5.5" r="3.5" style={{ fill: "var(--star)" }} />
      <path
        d="M12 9v12M8 11.5h8M4 14a8 8 0 0 0 16 0"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}
