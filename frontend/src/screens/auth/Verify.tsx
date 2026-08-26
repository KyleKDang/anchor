import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { api, messageOf } from "../../api";
import { useAuth } from "../../auth";
import { AuthCard } from "./AuthCard";

/** The landing of the emailed link: the password chosen at signup finishes the job. */
export function Verify() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const { loggedIn } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!token) {
    return (
      <AuthCard title="Verification failed">
        <p className="error" role="alert">
          This verification link is incomplete.
        </p>
        <p className="muted">
          <Link to="/signup">Sign up again</Link> to get a fresh link.
        </p>
      </AuthCard>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = await api.verify(token!, password);
      loggedIn(account);
      navigate("/", { replace: true });
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Finish signing up">
      <p>Enter the password you chose to verify your email and log in.</p>
      <form onSubmit={submit} className="form">
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="button" disabled={busy}>
          Verify and log in
        </button>
      </form>
      <p className="muted">
        Link expired or not yours? <Link to="/signup">Sign up again</Link> for a fresh one.
      </p>
    </AuthCard>
  );
}
