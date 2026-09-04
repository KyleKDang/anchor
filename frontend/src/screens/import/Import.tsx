import { Link } from "react-router";

import { Letterboxd } from "./Letterboxd";

/**
 * The import as a screen of its own: where the entry fork's first branch leads.
 *
 * The same section Profile carries, mounted on its own route, because sending a brand-new
 * account to a settings page to do the thing it just chose would read as a wrong turn.
 * Profile keeps its copy: the import stays reachable later, from where the rest of the
 * account's plumbing lives.
 */
export function Import() {
  // The heading deliberately does not repeat the section's own: "Import from Letterboxd"
  // over a "Letterboxd" heading is the same phrase twice, and the section already
  // carries every word of explanation this screen would otherwise duplicate.
  return (
    <>
      <h1>Bring your films across</h1>
      <Letterboxd />
      <p className="prompt-skip">
        <Link to="/warmup">Skip the import and start fresh</Link>
      </p>
    </>
  );
}
