import type { ReactNode } from "react";
import { Link } from "react-router";

import { Mark } from "../../Mark";

/**
 * The shared frame of the signup, login, and verification screens. A card drawn where no
 * route can render (the app failed to boot) sets `reloadHome`, so the wordmark loads the
 * page afresh rather than changing a URL nothing is listening to.
 */
export function AuthCard({
  title,
  reloadHome = false,
  children,
}: {
  title: string;
  reloadHome?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="auth">
      <section className="auth-card card" aria-labelledby="auth-title">
        {/* The wordmark leads home, as the frame's does; signed out, home is the front door. */}
        <Link className="wordmark" to="/" reloadDocument={reloadHome}>
          <Mark />
          Anchor
        </Link>
        <h1 id="auth-title">{title}</h1>
        {children}
      </section>
    </div>
  );
}
