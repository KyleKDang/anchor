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

  // Focus follows the interception, so a visitor on the keyboard is standing in the dialog
  // rather than still on the control that did nothing, and lands back on that control when
  // they close it. Tab is held inside, because `aria-modal` tells a screen reader the rest
  // of the page is not there and walking into it anyway is the worst of both.
  useEffect(() => {
    if (!open) return;
    const returnTo = document.activeElement;
    dialog.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab" || dialog.current === null) return;
      const stops = [...dialog.current.querySelectorAll<HTMLElement>("button")];
      const first = stops[0];
      const last = stops[stops.length - 1];
      if (first === undefined || last === undefined) return;
      // Anywhere but the dialog's own controls counts as leaving, which covers the dialog
      // box itself: it takes the opening focus and is not a tab stop of its own.
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const leaving = at === -1 || (event.shiftKey ? at === 0 : at === stops.length - 1);
      if (!leaving) return;
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (returnTo instanceof HTMLElement && returnTo.isConnected) returnTo.focus();
    };
  }, [open, close]);

  // Signing up means leaving the demo first: the signup screen is a visitor's screen, and
  // a session still holding the shared account would be redirected straight back off it.
  // A sign-out that fails leaves the pitch standing rather than sending anybody to a
  // screen that would bounce them, so the button is simply still there to press again.
  const signUp = useCallback(async () => {
    try {
      await logOut();
    } catch {
      return;
    }
    close();
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
