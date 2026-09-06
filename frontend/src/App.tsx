import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router";

import { api } from "./api";
import { RequireAccount, RequireVisitor } from "./auth";
import { destinations } from "./destinations";
import { Film } from "./screens/Film";
import { Place } from "./screens/Place";
import { Import } from "./screens/import/Import";
import { Review } from "./screens/import/Review";
import { Login } from "./screens/auth/Login";
import { Signup } from "./screens/auth/Signup";
import { Verify } from "./screens/auth/Verify";
import { Warmup } from "./screens/onboarding/Warmup";
import { Welcome } from "./screens/onboarding/Welcome";

export function App() {
  return (
    <Routes>
      <Route element={<RequireVisitor />}>
        <Route path="/signup" element={<Signup />} />
        <Route path="/login" element={<Login />} />
      </Route>
      <Route path="/verify" element={<Verify />} />
      <Route path="/debug/error" element={<DebugError />} />
      <Route element={<RequireAccount />}>
        {/* Full-screen and outside the frame: on the picker there is nothing to do
            but pick, so the navigation would only be a distraction. The entry fork
            is outside for the same reason, one step earlier - the five destinations
            have nothing in them yet, so showing them would be showing five dead ends. */}
        <Route path="/place/:tmdbId" element={<Place />} />
        <Route path="/welcome" element={<Welcome />} />
        <Route element={<Shell />}>
          <Route index element={<Home />} />
          {destinations.map(({ path, screen: Screen }) => (
            <Route key={path} path={path} element={<Screen />} />
          ))}
          {/* Not a destination: the film page is reached by tapping a film anywhere. */}
          <Route path="/films/:tmdbId" element={<Film />} />
          {/* Nor is the warmup, which is a flow to walk through and then leave; it is
              reached from the fork and, afterwards, from Profile. */}
          <Route path="/warmup" element={<Warmup />} />
          {/* Nor is the import: the fork's first branch leads here, and Profile's
              Letterboxd area carries the same section for every visit after that. */}
          <Route path="/import" element={<Import />} />
          {/* Nor is the import review: it is offered from Profile's Letterboxd area,
              and it is a queue to work through rather than somewhere to live. */}
          <Route path="/import/review" element={<Review />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/**
 * Where a logged-in account lands: the entry fork if it has never answered one.
 *
 * A read rather than a redirect rule written into the router, because "has this account
 * been asked yet?" is the server's fact and nothing on the client can derive it. It
 * answers once and then this is a plain redirect to the first destination forever.
 *
 * A failed read lands on the watchlist rather than blocking: onboarding is never a gate,
 * and that has to hold when onboarding itself is what is broken.
 */
function Home() {
  const [fork, setFork] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .warmup()
      .then((warmup) => setFork(warmup.fork))
      .catch(() => setFork(false));
  }, []);

  if (fork === null) return null;
  return <Navigate to={fork ? "/welcome" : destinations[0].path} replace />;
}

/** Fails on purpose: visiting this in production must produce a Sentry event. */
function DebugError(): never {
  throw new Error("deliberate frontend error to check Sentry");
}

/** The logged-in frame: the five destinations and the screen they open. */
function Shell() {
  const dots = useUnlockDots();
  return (
    <div className="app">
      <nav className="nav" aria-label="Main">
        {/* The wordmark leads home rather than to a sixth destination; on a phone the rail
            becomes a tab bar and it gives up its space to the five that go somewhere. */}
        <Link className="wordmark" to="/">
          Anchor
        </Link>
        {destinations.map(({ path, label }) => (
          <NavLink key={path} to={path}>
            {label}
            {dots.has(path) && (
              <>
                <span className="dot" aria-hidden="true" />
                <span className="visually-hidden"> - new</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}

/**
 * The one-time unlock dots, and the only nav-level marker in the product.
 *
 * Reserved for the two readiness unlocks - Discovery at *forming*, Watchlist at *ready*
 * - and cleared on the first visit to the screen each one points at. This only has to
 * re-ask on every navigation and leave the dot off the destination the owner is standing
 * on. Nothing else in Anchor ever gets one: no counts, no unread, no badge that grows.
 */
function useUnlockDots(): Set<string> {
  const { pathname } = useLocation();
  const [pending, setPending] = useState<Set<string>>(new Set());

  useEffect(() => {
    void (async () => {
      try {
        const unlocks = await api.unlocks();
        setPending(
          new Set([
            ...(unlocks.discovery ? ["/discovery"] : []),
            ...(unlocks.watchlist ? ["/watchlist"] : []),
          ]),
        );
      } catch {
        // A dot is the quietest thing on the screen; failing to fetch one is not worth
        // a banner, and the next navigation asks again.
      }
    })();
  }, [pathname]);

  return new Set([...pending].filter((path) => path !== pathname));
}
