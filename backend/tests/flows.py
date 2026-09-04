"""Driving Anchor's flows over the JSON API, in the vocabulary the design uses.

Tests read as what the owner did - mark a film watched, answer until it lands, designate
an anchor, keep comparing - rather than as endpoint calls, so they survive the endpoints
moving. Every driver asserts its own call succeeded, which keeps the failure at the step
that actually broke instead of three assertions later.

The advisory opponent picker takes a seed, so a scripted answer sequence lands the same
way every run; nothing here asserts which opponent it picked, because that is the
advisory math's business and the tests must not pin it (testing.md).
"""

from faketmdb import FilmFixture
from invariants import assert_no_rating_keys

LIBRARY = tuple(
    FilmFixture(1000 + n, f"Film {n:02d}", release_date=f"{1980 + n}-01-01") for n in range(12)
)
"""A dozen films: enough for a bisection deep enough to count, and for ten bands."""


async def account_id(client):
    response = await client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()["id"]


# --- Watching and placing ---


async def mark_watched(client, film, rate="later"):
    response = await client.post(f"/api/films/{film.tmdb_id}/watched", json={"rate": rate})
    assert response.status_code == 200, response.text
    return response.json()


async def begin(client, film, seed=1, **params):
    response = await client.post(f"/api/placements/{film.tmdb_id}", params={"seed": seed, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def answer(client, film, opponent_tmdb_id, verdict, seed=1):
    """Answer the question about this film and one opponent, as the flow showed the pair."""
    return await answer_pair(client, film, film.tmdb_id, opponent_tmdb_id, verdict, seed)


async def answer_pair(client, film, a_tmdb_id, b_tmdb_id, verdict, seed=1, expect=200):
    """Answer whatever pair the step showed, which is not always about ``film``.

    A quiet drift check rides in the placement of another film and is about two others
    entirely, so the answer echoes back the pair rather than naming an opponent.
    """
    response = await client.post(
        f"/api/placements/{film.tmdb_id}/answers",
        json={
            "a_tmdb_id": a_tmdb_id,
            "b_tmdb_id": b_tmdb_id,
            "verdict": verdict,
            "seed": seed,
        },
    )
    assert response.status_code == expect, response.text
    return response.json()


async def answer_band(client, film, band, exemplar_tmdb_id=None, seed=1):
    response = await client.post(
        f"/api/placements/{film.tmdb_id}/band",
        json={"band": band, "exemplar_tmdb_id": exemplar_tmdb_id, "seed": seed},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def bail(client, film):
    response = await client.post(f"/api/placements/{film.tmdb_id}/bail")
    assert response.status_code == 200, response.text
    return response.json()


async def keep_comparing(client, film, seed=1):
    response = await client.post(
        f"/api/placements/{film.tmdb_id}/keep-comparing", params={"seed": seed}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def place(client, film, verdict, seed=1, band=None, **params):
    """Watch a film and answer every question the same way until it lands.

    Returns the done screen and how many comparisons it took, which is how the bisection
    is measured without naming a single opponent. A band question along the way is
    answered with ``band`` where the caller supplied one, and with the first option
    offered where they did not, since most tests care about the position, not the stars.
    """
    await mark_watched(client, film, "now")
    step = await begin(client, film, seed, **params)
    asked = 0
    while not step["done"]:
        if step["kind"] == "band":
            chosen = band if band is not None else step["options"][0]["band"]
            exemplar = next(
                (option["exemplar"] for option in step["options"] if option["band"] == chosen), None
            )
            step = await answer_band(
                client, film, chosen, exemplar["tmdb_id"] if exemplar else None, seed
            )
            continue
        assert_no_rating_keys(step, "a mid-flow question")
        asked += 1
        step = await answer_pair(
            client, film, step["a"]["tmdb_id"], step["b"]["tmdb_id"], verdict, seed
        )
    return step, asked


async def place_at(client, film, ordering_ids, index, seed=1):
    """Answer every comparison so the film lands at ``index``, whatever opponents come.

    Returns whatever the flow stopped on - the done screen, or the band question, which
    is exactly the point where the position is settled and only the stars are open.
    """
    await mark_watched(client, film, "now")
    step = await begin(client, film, seed)
    while not step["done"] and step["kind"] == "comparison":
        assert_no_rating_keys(step, "a mid-flow question")
        opponent = step["b"]["tmdb_id"]
        verdict = "a" if ordering_ids.index(opponent) >= index else "b"
        step = await answer(client, film, opponent, verdict, seed)
    return step


async def replace_at(client, film, ordering_ids, index, seed=1):
    """The same, for a film already in the ordering: a re-placement the owner started."""
    step = await begin(client, film, seed)
    while not step["done"] and step["kind"] == "comparison":
        assert_no_rating_keys(step, "a mid-flow question")
        opponent = step["b"]["tmdb_id"]
        verdict = "a" if ordering_ids.index(opponent) >= index else "b"
        step = await answer(client, film, opponent, verdict, seed)
    return step


async def build_ordering(client, films):
    """Place films worst-last: each new one loses every comparison, so order is preserved."""
    for film in films:
        await place(client, film, "b")


# --- Anchors ---


async def designate(client, band, film, expect=200):
    response = await client.post(f"/api/anchors/{band}", json={"tmdb_id": film.tmdb_id})
    assert response.status_code == expect, response.text
    return response.json()


async def retire(client, band):
    response = await client.delete(f"/api/anchors/{band}")
    assert response.status_code == 204, response.text


async def anchors(client):
    response = await client.get("/api/anchors")
    assert response.status_code == 200, response.text
    return response.json()


async def anchored(client, band, film):
    """Place a film and make it a band's exemplar, which is the fresh-account bootstrap."""
    await place(client, film, "b")
    return await designate(client, band, film)


# --- Drift ---


async def re_place(client, film, expect=204):
    """Resolve a drift flag by choosing to re-place: "my opinion changed"."""
    response = await client.post(f"/api/drift/{film.tmdb_id}/re-place")
    assert response.status_code == expect, response.text


async def keep_position(client, film, opponents=None, expect=204):
    """Resolve by keeping the position, with the per-opponent follow-up answered."""
    response = await client.post(
        f"/api/drift/{film.tmdb_id}/keep", json={"opponents": opponents or []}
    )
    assert response.status_code == expect, response.text


async def flag_of(client, film):
    """The open drift flag on a film's page, or None where the owner has none to see."""
    return (await film_page(client, film))["drift"]


# --- Rewatches ---


async def log_rewatch(client, film, expect=200):
    """Mark an already-rated film watched, which is the rewatch flow."""
    response = await client.post(f"/api/films/{film.tmdb_id}/watched", json={})
    assert response.status_code == expect, response.text
    return response.json()


async def answer_rewatch(client, film, answer, expect=204):
    response = await client.post(f"/api/rewatches/{film.tmdb_id}", json={"answer": answer})
    assert response.status_code == expect, response.text


# --- Reading the screens ---


async def rated(client, **params):
    response = await client.get("/api/rated", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def film_page(client, film):
    response = await client.get(f"/api/films/{film.tmdb_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def profile(client):
    response = await client.get("/api/profile")
    assert response.status_code == 200, response.text
    return response.json()


# --- The criteria bonus card ---


async def ask_criteria(client, frequency):
    """Set how often the bonus question is offered, ``off`` included."""
    response = await client.put("/api/profile/criteria", json={"frequency": frequency})
    assert response.status_code == 200, response.text
    return response.json()


async def answer_criteria(client, card, verdict, expect=204):
    """Answer the bonus card. Not answering is the other half of the flow, and is nothing."""
    response = await client.post(f"/api/criteria/{card['id']}", json={"verdict": verdict})
    assert response.status_code == expect, response.text
    return response.json() if expect not in (204,) else None


async def backlog(client, **params):
    response = await client.get("/api/watchlist/backlog", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def add_to_backlog(client, film):
    response = await client.post(f"/api/films/{film.tmdb_id}/backlog")
    assert response.status_code == 200, response.text
    return response.json()


async def remove_from_backlog(client, film, expect=204):
    response = await client.delete(f"/api/films/{film.tmdb_id}/backlog")
    assert response.status_code == expect, response.text


# --- The ranked tier ---


async def tier(client, *, boundary=True):
    """Read the Watchlist's top half - which is also what maintains it (watchlist.md).

    ``boundary=False`` is the screen reloading after the owner's own action: the read
    shows what the action did and leaves the engine's own maintenance to the next visit.
    """
    params = {} if boundary else {"boundary": "false"}
    response = await client.get("/api/watchlist/tier", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def pin(client, film, expect=204):
    response = await client.post(f"/api/watchlist/{film.tmdb_id}/pin")
    assert response.status_code == expect, response.text


async def unpin(client, film, expect=204):
    response = await client.delete(f"/api/watchlist/{film.tmdb_id}/pin")
    assert response.status_code == expect, response.text


async def veto(client, film, expect=204):
    response = await client.post(f"/api/watchlist/{film.tmdb_id}/veto")
    assert response.status_code == expect, response.text


async def lift_veto(client, film, expect=204):
    response = await client.delete(f"/api/watchlist/{film.tmdb_id}/veto")
    assert response.status_code == expect, response.text


async def not_now(client, film, expect=204):
    response = await client.post(f"/api/watchlist/{film.tmdb_id}/not-now")
    assert response.status_code == expect, response.text


async def unlocks(client):
    response = await client.get("/api/unlocks")
    assert response.status_code == 200, response.text
    return response.json()


async def log_watches(client, films):
    """Advance the watch clock without touching the backlog: the only clock the tier reads.

    Every cooldown and staleness measure is denominated in this, so a test that wants
    time to pass logs watches - there is no calendar clock anywhere to freeze (testing.md).
    """
    for film in films:
        await mark_watched(client, film, "later")


def tier_ids(payload):
    """Every seated film, up-next zone first: the tier read as one ordered list."""
    return [film["tmdb_id"] for film in payload["up_next"] + payload["pool"]]


def stage_of(payload, state):
    """One readiness state's row on the Profile screen, with its bars."""
    return next(stage for stage in payload["stages"] if stage["state"] == state)


def ordering_of(payload):
    """The ordering as plain ids, flattened back out of its band grouping."""
    return [
        [film["tmdb_id"] for film in slot] for group in payload["groups"] for slot in group["slots"]
    ]


def bands_of(payload):
    """Every rated film's derived band, however the screen happened to be sorted."""
    listed = payload["films"] or [
        film for group in payload["groups"] for slot in group["slots"] for film in slot
    ]
    return {film["tmdb_id"]: film["band"] for film in listed}


def queue_of(payload):
    return [film["tmdb_id"] for film in payload["rate_later"]]


# --- The seed import ---


async def upload_export(client, data, name=None, confirm=None, expect=202):
    """Upload an export zip as the raw request body, which is the whole of the API."""
    import export as export_module

    params = {"name": name or export_module.NAME}
    if confirm is not None:
        params["confirm"] = confirm
    response = await client.post(
        "/api/import",
        params=params,
        content=data,
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == expect, response.text
    return response.json()


async def import_state(client):
    response = await client.get("/api/import")
    assert response.status_code == 200, response.text
    return response.json()


async def review_queue(client):
    response = await client.get("/api/import/review")
    assert response.status_code == 200, response.text
    return response.json()


async def unmatched(client):
    response = await client.get("/api/import/unmatched")
    assert response.status_code == 200, response.text
    return response.json()


async def bind_row(client, row_id, tmdb_id, expect=200):
    response = await client.post(f"/api/import/rows/{row_id}/film", json={"tmdb_id": tmdb_id})
    assert response.status_code == expect, response.text
    return response.json() if expect != 204 else None


async def rescue_row(client, row_id, expect=200):
    response = await client.post(f"/api/import/rows/{row_id}/letterboxd")
    assert response.status_code == expect, response.text
    return response.json()


async def dismiss_row(client, row_id, expect=204):
    response = await client.delete(f"/api/import/rows/{row_id}")
    assert response.status_code == expect, response.text


async def reset_warning(client):
    response = await client.get("/api/import/warning")
    assert response.status_code == 200, response.text
    return response.json()
