import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";

import { onRefusedWrite } from "./api";
import { useAuth } from "./auth";

/**
 * The account screens, where a demo session is allowed for the one purpose of leaving
 * (`RequireVisitor`), and the verification that follows signing up. A visitor there is
 * already on their way out, so the strip below stays off them.
 */
const DOORS = new Set(["/signup", "/login", "/verify", "/demo"]);

/**
 * The strip: the demo saying what it is on every screen, with the way out beside it.
 *
 * Mounted once above the router like the intercept, and for the same reason: the picker
 * and the criteria session render outside the frame, and a visitor is as much in the demo
 * there as on the wall. It answers the question the intercept cannot - a visitor who wants
 * the door has not pressed anything - so that finding the exit never means guessing that
 * Profile is where it is kept.
 *
 * A status line and not a banner (ADR 0011): it never changes and never asks for anything,
 * so it is furniture. "Leave the demo" is the same `logOut` Profile calls, which for a demo
 * session lands on the front door with no notice; "Build your own" is the intercept's own
 * link to signup, a plain link for the same reason - there is nothing to sign out of first.
 */
export function DemoStrip() {
  const { account, logOut } = useAuth();
  const { pathname } = useLocation();
  if (!account?.demo || DOORS.has(pathname)) return null;
  return (
    <aside className="demo-strip" aria-label="Demo">
      <div className="demo-strip-inner">
        <p className="demo-strip-copy">
          <strong>Demo account</strong>
          {/* The sentence gives way on a phone, where one line holds the label and the
              two verbs and nothing more. */}
          <span className="demo-strip-more"> - look anywhere, nothing you press changes it.</span>
        </p>
        <button type="button" className="link-button" onClick={() => void logOut()}>
          Leave the demo
        </button>
        <span className="demo-strip-dot" aria-hidden="true">
          ·
        </span>
        <Link className="link-button" to="/signup">
          Build your own
        </Link>
      </div>
    </aside>
  );
}

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
      const stops = [...dialog.current.querySelectorAll<HTMLElement>("button, a[href]")];
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
          Everything on these screens is a lived-in account - so look anywhere you like, but nothing
          you press changes it.
        </p>
        <p className="muted">
          Sign up and the wall, the watchlist and the feed become yours instead.
        </p>
        <div className="actions">
          <button type="button" className="button secondary" onClick={close}>
            Keep looking
          </button>
          {/* A plain link, and deliberately not a sign-out first: a demo session is
              allowed onto the signup screen, so there is nothing to undo before going
              there and no redirect to race. Signing up replaces the cookie. */}
          <Link className="button" to="/signup" onClick={close}>
            Build your own
          </Link>
        </div>
      </div>
    </div>
  );
}
