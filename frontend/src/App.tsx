import { Navigate, NavLink, Route, Routes } from "react-router";

import { destinations } from "./destinations";

export function App() {
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
        <Routes>
          <Route index element={<Navigate to={destinations[0].path} replace />} />
          {destinations.map(({ path, screen: Screen }) => (
            <Route key={path} path={path} element={<Screen />} />
          ))}
        </Routes>
      </main>
    </div>
  );
}
