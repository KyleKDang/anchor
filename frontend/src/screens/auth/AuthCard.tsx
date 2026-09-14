import type { ReactNode } from "react";
import { Link } from "react-router";

import { Mark } from "../../Mark";

/** The shared frame of the signup, login, and verification screens. */
export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="auth">
      <section className="auth-card card" aria-labelledby="auth-title">
        {/* The wordmark leads home, as the frame's does; signed out, home is the front door. */}
        <Link className="wordmark" to="/">
          <Mark />
          Anchor
        </Link>
        <h1 id="auth-title">{title}</h1>
        {children}
      </section>
    </div>
  );
}
