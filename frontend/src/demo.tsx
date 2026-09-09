import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { onRefusedWrite } from "./api";
import { useAuth } from "./auth";

/**
 * The read-only demo's one intercept: what comes up when a visitor presses a write.
 *
 * Mounted once, above the router, because the intercept belongs to the session rather
 * than to any screen - the picker and the criteria session render outside the frame, and
 * a visitor meets the same thing there as on the wall. Nothing here knows which control
 * was pressed: every write in the app goes through one client seam and that seam raises
 * this, so there is one pitch to write and one place to change its wording.
 *
 * A visitor pressing "pin" has just shown they understood what pinning is for, which is
 * the best moment there will be to offer them an account of their own - so the intercept
 * is where the signup pitch belongs, and the verbs stay on screen to be pressed
 * (demo-account.md). The one control this does not cover is the wall's edit mode, which
 * is absent rather than intercepted: a wall that invites dragging and then refuses every
 * drop is a broken toy rather than a pitch.
 *
 * The one interruption in a product that otherwise never interrupts (ADR 0011). It is
 * allowed because it is the visitor's own press that raises it and it is answering them.
 */
export function ReadOnlyPitch() {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const { logOut } = useAuth();
  const navigate = useNavigate();
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    onRefusedWrite(() => setOpen(true));
    return () => onRefusedWrite(null);
  }, []);

  // Focus follows the interception, so a visitor on the keyboard is standing in the
  // dialog rather than still on the control that did nothing, and Escape leaves.
  useEffect(() => {
    if (!open) return;
    dialog.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  // Signing up means leaving the demo first: the signup screen is a visitor's screen, and
  // a session still holding the shared account would be redirected straight back off it.
  const signUp = useCallback(async () => {
    close();
    await logOut();
    void navigate("/signup");
  }, [close, logOut, navigate]);

  if (!open) return null;
  return (
    <div className="scrim" onClick={close}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="read-only-demo"
        tabIndex={-1}
        ref={dialog}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="read-only-demo">This is a read-only demo</h2>
        <p>
          Everything on these screens is a real account, built out of one person&rsquo;s judgments -
          so look anywhere you like, but nothing you press changes it.
        </p>
        <p className="muted">
          Sign up and the wall, the watchlist and the feed become yours instead.
        </p>
        <div className="actions">
          <button type="button" className="button secondary" onClick={close}>
            Keep looking
          </button>
          <button type="button" className="button" onClick={() => void signUp()}>
            Build your own
          </button>
        </div>
      </div>
    </div>
  );
}
