import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { api, ApiError, messageOf, readOnlySession, type Account } from "./api";
import { AuthCard } from "./screens/auth/AuthCard";

interface Auth {
  /** `undefined` while the session is still being looked up, `null` when logged out. */
  account: Account | null | undefined;
  /** A line for the login screen after a deliberate sign-out ("You are logged out."). */
  notice: string | null;
  /** The session ended on purpose rather than expiring under the visitor. */
  left: boolean;
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

  // The client's copy of the demo flag, which is what puts the intercept in front of
  // every write rather than behind it. The server refuses these anyway and is the source
  // of truth; this only means a visitor gets the pitch instead of a round trip.
  useEffect(() => readOnlySession(account?.demo ?? false), [account]);

  // Whether the session ended on purpose. A lost session comes back to where it was; a
  // deliberate exit does not, and where it goes instead depends on what was signed out.
  const [left, setLeft] = useState(false);

  const loggedIn = useCallback((signedIn: Account) => {
    setAccount(signedIn);
    setNotice(null);
    setLeft(false);
  }, []);

  const loggedOut = useCallback((reason: string | null) => {
    setAccount(null);
    setNotice(reason);
    setLeft(true);
  }, []);

  const logOut = useCallback(async () => {
    const wasDemo = account?.demo ?? false;
    await api.logOut();
    // A visitor leaving the demo was never logged in as anybody, so there is nothing to
    // say and nowhere to say it: they go back to the front door they came in by.
    loggedOut(wasDemo ? null : "You are logged out.");
  }, [account, loggedOut]);

  const accountDeleted = useCallback(
    () => loggedOut("Your account has been deleted."),
    [loggedOut],
  );

  const value = useMemo(
    () => ({ account, notice, left, loggedIn, logOut, accountDeleted }),
    [account, notice, left, loggedIn, logOut, accountDeleted],
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
  const { account, notice, left } = useAuth();
  const location = useLocation();
  if (account === undefined) return null;
  if (account === null) {
    // A visitor who put the demo down goes back to the front door; an owner who signed
    // out gets the login screen and its line; a lost session comes back to where it was.
    if (left && notice === null) return <Navigate to="/" replace />;
    const state = notice ? undefined : { from: location.pathname };
    return <Navigate to="/login" replace state={state} />;
  }
  return <Outlet />;
}

/**
 * The opposite guard: a logged-in owner has no business on the auth screens.
 *
 * A demo session is the exception, and it is the whole point of the demo. Somebody looking
 * around the shared account is a visitor in every sense that matters here - the session
 * belongs to nobody - and the pitch's "build your own" leads straight to signup. Bouncing
 * them off it would leave the demo with no way out except signing out first, which is a
 * redirect race and an ugly "you are logged out" on the way to a screen they asked for.
 * Signing up simply replaces the cookie, and the demo is none the wiser.
 */
export function RequireVisitor() {
  const { account } = useAuth();
  if (account === undefined) return null;
  if (account && !account.demo) return <Navigate to="/" replace />;
  return <Outlet />;
}
