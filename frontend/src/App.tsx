import { Navigate, NavLink, Outlet, Route, Routes } from "react-router";

import { RequireAccount, RequireVisitor } from "./auth";
import { destinations } from "./destinations";
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
        <Route element={<Shell />}>
          <Route index element={<Navigate to={destinations[0].path} replace />} />
          {destinations.map(({ path, screen: Screen }) => (
            <Route key={path} path={path} element={<Screen />} />
          ))}
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
  return (
    <div className="app">
      <nav className="nav" aria-label="Main">
        {destinations.map(({ path, label }) => (
          <NavLink key={path} to={path}>
            {label}
          </NavLink>
        ))}
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
