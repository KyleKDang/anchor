import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { api, ApiError, messageOf, type Account } from "./api";
import { AuthCard } from "./screens/auth/AuthCard";

interface Auth {
  /** `undefined` while the session is still being looked up, `null` when logged out. */
  account: Account | null | undefined;
  /** A line for the login screen after a deliberate sign-out ("You are logged out."). */
  notice: string | null;
  /** The API just logged this account in (login or verification). */
  loggedIn: (account: Account) => void;
  logOut: () => Promise<void>;
  /** The account was just deleted; the guards send the visitor to login. */
  accountDeleted: () => void;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  const [notice, setNotice] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => !cancelled && setAccount(me))
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) setAccount(null);
        else setBootError(messageOf(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loggedIn = useCallback((signedIn: Account) => {
    setAccount(signedIn);
    setNotice(null);
  }, []);

  const loggedOut = useCallback((reason: string) => {
    setAccount(null);
    setNotice(reason);
  }, []);

  const logOut = useCallback(async () => {
    await api.logOut();
    loggedOut("You are logged out.");
  }, [loggedOut]);

  const accountDeleted = useCallback(() => loggedOut("Your account has been deleted."), [loggedOut]);

  const value = useMemo(
    () => ({ account, notice, loggedIn, logOut, accountDeleted }),
    [account, notice, loggedIn, logOut, accountDeleted],
  );

  if (bootError) return <Unavailable message={bootError} />;
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** The session lookup failed for a reason other than "not logged in": nothing else can render. */
function Unavailable({ message }: { message: string }) {
  return (
    <AuthCard title="Anchor is unavailable">
      <p className="error" role="alert">
        {message}
      </p>
      <button type="button" className="button" onClick={() => window.location.reload()}>
        Try again
      </button>
    </AuthCard>
  );
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (auth === null) throw new Error("useAuth outside AuthProvider");
  return auth;
}

/** Renders its children only for a logged-in account; sends visitors to the login screen. */
export function RequireAccount() {
  const { account, notice } = useAuth();
  const location = useLocation();
  if (account === undefined) return null;
  if (account === null) {
    // A deliberate sign-out starts over at the front door; a lost session comes back here.
    const state = notice ? undefined : { from: location.pathname };
    return <Navigate to="/login" replace state={state} />;
  }
  return <Outlet />;
}

/** The opposite guard: a logged-in owner has no business on the auth screens. */
export function RequireVisitor() {
  const { account } = useAuth();
  if (account === undefined) return null;
  if (account) return <Navigate to="/" replace />;
  return <Outlet />;
}
