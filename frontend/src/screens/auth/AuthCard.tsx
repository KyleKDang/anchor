import type { ReactNode } from "react";

import { Mark } from "../../Mark";

/** The shared frame of the signup, login, and verification screens. */
export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="auth">
      <section className="auth-card card" aria-labelledby="auth-title">
        <p className="wordmark">
          <Mark />
          Anchor
        </p>
        <h1 id="auth-title">{title}</h1>
        {children}
      </section>
    </div>
  );
}
