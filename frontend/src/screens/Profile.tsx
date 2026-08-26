import { useState, type FormEvent } from "react";

import { api, messageOf } from "../api";
import { useAuth } from "../auth";

export function Profile() {
  return (
    <>
      <h1>Profile</h1>
      <AccountSection />
    </>
  );
}

function AccountSection() {
  const { account, logOut, accountDeleted } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleDelete(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.deleteAccount(password);
      accountDeleted();
    } catch (caught) {
      setError(messageOf(caught));
      setBusy(false);
    }
  }

  return (
    <section className="section" aria-labelledby="account-heading">
      <h2 id="account-heading">Account</h2>
      <p>
        Logged in as <strong>{account?.email}</strong>.
      </p>
      <button type="button" className="button secondary" onClick={() => void logOut()}>
        Log out
      </button>

      <form onSubmit={handleDelete} className="form danger-zone" aria-labelledby="delete-heading">
        <h3 id="delete-heading">Delete account</h3>
        <p className="muted">
          This deletes your account and everything in it: ratings, comparisons, watchlist, taste
          profile. There is no undo.
        </p>
        <label className="field">
          <span>Confirm your password</span>
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
        <button type="submit" className="button danger" disabled={busy}>
          Delete account
        </button>
      </form>
    </section>
  );
}
