import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, messageOf } from "../api";
import { useAuth } from "../auth";
import { AuthCard } from "./auth/AuthCard";

/**
 * The door onto the demo: `/demo` opens a session on the shared account and lands on
 * Discovery, the hero surface (demo-account.md, "Surfaces").
 *
 * A route rather than a button's click handler so the door has an address - a recruiter
 * can be sent straight to it - and so the landing page's verb is a plain link like its
 * neighbours. There is nothing to show on the way through: the session opens in one
 * round trip and the visitor is in the feed. Only a door that would not open renders
 * anything, and it says so without dressing it up.
 *
 * No welcome overlay and no tour on the far side, by design: a demo that needs a tour
 * undercuts the self-explanatory claim the demo exists to make.
 */
export function Demo() {
  const { loggedIn } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .enterDemo()
      .then((account) => {
        if (cancelled) return;
        loggedIn(account);
        navigate("/discovery", { replace: true });
      })
      .catch((caught: unknown) => !cancelled && setError(messageOf(caught)));
    return () => {
      cancelled = true;
    };
  }, [loggedIn, navigate]);

  if (error === null) return null;
  return (
    <AuthCard title="The demo is not open right now">
      <p className="error" role="alert">
        {error}
      </p>
      <p className="muted">
        <Link to="/">Back to the front door</Link>
      </p>
    </AuthCard>
  );
}
