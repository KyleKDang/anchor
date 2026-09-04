import { Link } from "react-router";

import { api, type BacklogPhase, type SearchResult, type Warmup } from "../../api";
import { useAsyncAction } from "../../films/useAsyncAction";
import { FilmPicker } from "./FilmPicker";

/**
 * Phase 3: something to watch next, which is the one feature usable from minute one.
 *
 * On the import fill this has already happened - watchlist.csv seeded the backlog before
 * the owner got here - so the phase reports rather than asks. On the fresh fill it is a
 * search, and no more than that: discovery fills the backlog once the taste profile has
 * enough to go on, and until then a suggestion would be a popularity list in disguise.
 */
export function SeedBacklog({
  phase,
  fill,
  onChanged,
}: {
  phase: BacklogPhase;
  fill: Warmup["fill"];
  onChanged: (warmup: Warmup) => void;
}) {
  const { busy, error, run } = useAsyncAction();

  async function add(film: SearchResult) {
    await run(async () => {
      await api.addToBacklog(film.tmdb_id);
      onChanged(await api.warmup());
    });
  }

  if (fill === "imported" && phase.seeded > 0) {
    return (
      <>
        <p>
          {phase.seeded} film{phase.seeded === 1 ? "" : "s"} from your Letterboxd watchlist
          {phase.seeded === 1 ? " is" : " are"} already in your backlog.
        </p>
        <p className="muted">
          It is ranked once Anchor knows your taste well enough to mean it. Until then it is
          honestly just the list.
        </p>
        <p>
          <Link className="button secondary" to="/watchlist">
            See your backlog
          </Link>
        </p>
      </>
    );
  }

  return (
    <>
      <p className="muted">
        Add a few films you have been meaning to watch. This is your backlog, and it works
        from the moment it has something in it.
      </p>
      {phase.films > 0 && (
        <p className="prompt-step muted">
          {phase.films} film{phase.films === 1 ? "" : "s"} in your backlog
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <FilmPicker
        label="Find something to watch"
        action="Add to backlog"
        disabled={busy}
        onPick={add}
        pickable={(film) => film.state === null}
      />
    </>
  );
}
