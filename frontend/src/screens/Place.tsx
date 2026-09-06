import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api, messageOf, type Landed as LandedStep, type Picker as PickerStep } from "../api";
import { Landed, Picker } from "../films/steps";

/** Where the flow's two exits lead: back where it was opened from, or Rated by default. */
const EXITS = ["/rated", "/warmup"] as const;

/**
 * The screen that sent the owner here, if it named itself and is one we recognise.
 *
 * An allowlist rather than "any path starting with a slash": this value comes out of the
 * URL bar, and the one thing a redirect target must never be is arbitrary.
 */
function useExit(): string {
  const [params] = useSearchParams();
  const asked = params.get("back");
  return EXITS.find((path) => path === asked) ?? EXITS[0];
}

/**
 * The band picker: a full-screen flow with one thing to do on it.
 *
 * It sits outside the app frame on purpose - no navigation, nothing else on screen - so
 * the only thing to do is pick. Each band row carries its anchor pool, which is what
 * makes the pick a judgment against the owner's own references rather than against a
 * remembered absolute scale (ADR 0013).
 *
 * There is no undo, deliberately. The wall is the correction path, and it is one tap
 * away from the done screen. Leaving without picking is free and safe too: the film is
 * already watched-unrated and already seated in the rate-later queue, so walking away
 * costs nothing and the picker keeps no state to lose.
 */
export function Place() {
  const { tmdbId } = useParams();
  const id = Number(tmdbId);
  const navigate = useNavigate();
  const exit = useExit();
  const [picker, setPicker] = useState<PickerStep | null>(null);
  const [landed, setLanded] = useState<LandedStep | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback(async () => {
    try {
      setPicker(await api.picker(id));
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [id]);

  useEffect(() => {
    void open();
  }, [open]);

  const leave = (
    <p className="muted place-leave">
      <Link to={exit}>Rate this later</Link> - it stays on your rate-later list.
    </p>
  );

  return (
    <div className="place">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {picker === null && landed === null && !error && <p className="muted">Loading…</p>}
      {picker !== null && landed === null && (
        <Picker picker={picker} tmdbId={id} onLanded={setLanded} footer={leave} />
      )}
      {landed !== null && (
        <Landed
          landed={landed}
          primary={
            // The wall is where a rating is corrected, so the way on from a landing is
            // the wall with this film picked out on it - not an undo button.
            <button
              type="button"
              className="button"
              onClick={() => void navigate(`/rated?film=${landed.film.tmdb_id}`)}
            >
              Adjust on the wall
            </button>
          }
          footer={
            <p className="muted place-leave">
              <Link to={exit}>Leave it where it is</Link>
            </p>
          }
        />
      )}
    </div>
  );
}
