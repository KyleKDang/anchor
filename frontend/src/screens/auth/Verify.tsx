import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, ApiError } from "../../api";
import { AuthCard } from "./AuthCard";

type State = { kind: "verifying" } | { kind: "verified"; email: string } | { kind: "failed"; message: string };

/** The landing of the emailed link: verifies the token once, then points at login. */
export function Verify() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const started = useRef(false);
  const [state, setState] = useState<State>(
    token ? { kind: "verifying" } : { kind: "failed", message: "This verification link is incomplete." },
  );

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    api
      .verify(token)
      .then((account) => setState({ kind: "verified", email: account.email }))
      .catch((caught: unknown) =>
        setState({
          kind: "failed",
          message: caught instanceof ApiError ? caught.message : "Something went wrong.",
        }),
      );
  }, [token]);

  if (state.kind === "verifying") {
    return (
      <AuthCard title="Verifying your email">
        <p className="muted">One moment.</p>
      </AuthCard>
    );
  }
  if (state.kind === "verified") {
    return (
      <AuthCard title="Email verified">
        <p>
          <strong>{state.email}</strong> is verified. You can log in now.
        </p>
        <Link to="/login" className="button">
          Log in
        </Link>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Verification failed">
      <p className="error" role="alert">
        {state.message}
      </p>
      <p className="muted">
        <Link to="/signup">Sign up again</Link> to get a fresh link.
      </p>
    </AuthCard>
  );
}
