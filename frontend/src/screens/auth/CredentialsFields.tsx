import type { Credentials } from "../../api";

interface Props {
  value: Credentials;
  onChange: (value: Credentials) => void;
  /** Signup announces the length rule and asks for a new password; login does not. */
  newPassword?: boolean;
}

/** The email and password fields the signup and login forms share. */
export function CredentialsFields({ value, onChange, newPassword = false }: Props) {
  return (
    <>
      <label className="field">
        <span>Email</span>
        <input
          type="email"
          name="email"
          autoComplete="email"
          required
          value={value.email}
          onChange={(event) => onChange({ ...value, email: event.target.value })}
        />
      </label>
      <label className="field">
        <span>Password</span>
        <input
          type="password"
          name="password"
          autoComplete={newPassword ? "new-password" : "current-password"}
          required
          minLength={newPassword ? 8 : undefined}
          maxLength={128}
          value={value.password}
          onChange={(event) => onChange({ ...value, password: event.target.value })}
        />
        {newPassword && <span className="hint">At least 8 characters.</span>}
      </label>
    </>
  );
}
