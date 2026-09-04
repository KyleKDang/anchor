import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router";

import { api } from "./api";
import { RequireAccount, RequireVisitor } from "./auth";
import { destinations } from "./destinations";
import { Film } from "./screens/Film";
import { Place } from "./screens/Place";
import { Review } from "./screens/import/Review";
import { Login } from "./screens/auth/Login";
import { Signup } from "./screens/auth/Signup";
import { Verify } from "./screens/auth/Verify";

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
        {/* Full-screen and outside the frame: mid-placement there is nothing to do
            but answer, so the navigation would only be a distraction. */}
        <Route path="/place/:tmdbId" element={<Place />} />
        <Route element={<Shell />}>
          <Route index element={<Navigate to={destinations[0].path} replace />} />
          {destinations.map(({ path, screen: Screen }) => (
            <Route key={path} path={path} element={<Screen />} />
          ))}
          {/* Not a destination: the film page is reached by tapping a film anywhere. */}
          <Route path="/films/:tmdbId" element={<Film />} />
          {/* Nor is the import review: it is offered from Profile's Letterboxd area,
              and it is a queue to work through rather than somewhere to live. */}
          <Route path="/import/review" element={<Review />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
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
 * Reserved for the two readiness unlocks and cleared on the first visit, which happens
 * server-side when the screen itself is read - so this only has to re-ask on every
 * navigation and leave the dot off the destination the owner is standing on. Nothing
 * else in Anchor ever gets one: no counts, no unread, no badge that grows.
 */
function useUnlockDots(): Set<string> {
  const { pathname } = useLocation();
  const [pending, setPending] = useState<Set<string>>(new Set());

  useEffect(() => {
    void (async () => {
      try {
        const unlocks = await api.unlocks();
        setPending(new Set(unlocks.watchlist ? ["/watchlist"] : []));
      } catch {
        // A dot is the quietest thing on the screen; failing to fetch one is not worth
        // a banner, and the next navigation asks again.
      }
    })();
  }, [pathname]);

  return new Set([...pending].filter((path) => path !== pathname));
}
