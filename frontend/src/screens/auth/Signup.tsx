import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { api, ApiError } from "../../api";
import { AuthCard } from "./AuthCard";

export function Signup() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = await api.signUp(email, password);
      setSentTo(account.email);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (sentTo) {
    return (
      <AuthCard title="Check your email">
        <p>
          We sent a verification link to <strong>{sentTo}</strong>. Open it to finish signing up.
        </p>
        <p className="muted">
          Nothing arrived? Signing up again with the same email sends a fresh link.
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Create your account">
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
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <span className="hint">At least 8 characters.</span>
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="button" disabled={busy}>
          Sign up
        </button>
      </form>
      <p className="muted">
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </AuthCard>
  );
}
