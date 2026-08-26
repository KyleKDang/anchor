import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { api, ApiError } from "../../api";
import { useAuth } from "../../auth";
import { AuthCard } from "./AuthCard";

export function Login() {
  const { notice, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [noticeDismissed, setNoticeDismissed] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNoticeDismissed(true);
    try {
      const account = await api.logIn(email, password);
      signIn(account);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from ?? "/", { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Something went wrong.");
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Log in">
      {notice && !noticeDismissed && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <form onSubmit={submit} className="form">
        <label className="field">
          <span>Email</span>
          <input
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
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
          Log in
        </button>
      </form>
      <p className="muted">
        New here? <Link to="/signup">Sign up</Link>
      </p>
    </AuthCard>
  );
}
