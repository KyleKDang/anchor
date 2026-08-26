import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { api, messageOf, type Credentials } from "../../api";
import { AuthCard } from "./AuthCard";
import { CredentialsFields } from "./CredentialsFields";

export function Signup() {
  const [credentials, setCredentials] = useState<Credentials>({ email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = await api.signUp(credentials);
      setSentTo(account.email);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  if (sentTo) {
    return (
      <AuthCard title="Check your email">
        <p>
          We sent a link to <strong>{sentTo}</strong>. Open it and enter your password to finish
          signing up.
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
        <CredentialsFields value={credentials} onChange={setCredentials} newPassword />
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
