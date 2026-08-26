import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { api, messageOf, type Credentials } from "../../api";
import { useAuth } from "../../auth";
import { AuthCard } from "./AuthCard";
import { CredentialsFields } from "./CredentialsFields";

export function Login() {
  const { notice, loggedIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [credentials, setCredentials] = useState<Credentials>({ email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [noticeDismissed, setNoticeDismissed] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNoticeDismissed(true);
    try {
      const account = await api.logIn(credentials);
      loggedIn(account);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from ?? "/", { replace: true });
    } catch (caught) {
      setError(messageOf(caught));
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
        <CredentialsFields value={credentials} onChange={setCredentials} />
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
