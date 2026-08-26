import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { api, ApiError, type Account } from "./api";

interface Auth {
  /** `undefined` while the session is still being looked up, `null` when logged out. */
  account: Account | null | undefined;
  /** A line for the login screen after a deliberate sign-out ("You are logged out."). */
  notice: string | null;
  signIn: (account: Account) => void;
  logOut: () => Promise<void>;
  /** Forget the account after it was deleted; the guards send the visitor to login. */
  forgetDeletedAccount: () => void;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => !cancelled && setAccount(me))
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) setAccount(null);
        else throw error;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback((signedIn: Account) => {
    setAccount(signedIn);
    setNotice(null);
  }, []);

  const signOut = useCallback((reason: string) => {
    setAccount(null);
    setNotice(reason);
  }, []);

  const logOut = useCallback(async () => {
    await api.logOut();
    signOut("You are logged out.");
  }, [signOut]);

  const forgetDeletedAccount = useCallback(() => signOut("Your account has been deleted."), [signOut]);

  const value = useMemo(
    () => ({ account, notice, signIn, logOut, forgetDeletedAccount }),
    [account, notice, signIn, logOut, forgetDeletedAccount],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
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
