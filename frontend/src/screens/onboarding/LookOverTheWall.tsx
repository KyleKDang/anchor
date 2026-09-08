import { Link } from "react-router";

import type { WallPhase } from "../../api";

/**
 * The import fill's middle step: the wall the export just built, in edit mode.
 *
 * A link and a count, because the step happens on another screen. Sending the owner to
 * Rated with edit mode already on is the whole of it - the explanation of dragging and
 * marking waits for them there, where the dragging is, rather than being read here and
 * remembered on the way over.
 *
 * The count says how far they have got and never that they are behind: the wall was
 * theirs to edit as much or as little as they liked before this step existed, and what
 * completes the step is having used the gesture, not having got the ordering right.
 */
export function LookOverTheWall({ phase }: { phase: WallPhase }) {
  return (
    <>
      <p className="muted">
        Your export landed every film in the band you rated it. Open the wall and move a few - drag
        a poster inside its row to say which of two you prefer, or into another band to change its
        rating.
      </p>
      <p className="prompt-step muted">
        {phase.moved === 0
          ? `Nothing moved yet. A few is about ${phase.target}.`
          : `${phase.moved} of about ${phase.target} moved so far.`}
      </p>
      <p>
        {/* The same URL the wall's own toggle writes, so arriving here from the step and
            arriving from the toggle land on one shape rather than two. */}
        <Link className="button secondary" to="/rated?edit=1">
          Open the wall
        </Link>
      </p>
    </>
  );
}
