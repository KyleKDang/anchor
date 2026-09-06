"""Driving Anchor's flows over the JSON API, in the vocabulary the design uses.

Tests read as what the owner did - mark a film watched, rate it, re-rate it, mark an
anchor - rather than as endpoint calls, so they survive the endpoints moving. Every
driver asserts its own call succeeded, which keeps the failure at the step that actually
broke instead of three assertions later.
"""

from faketmdb import FilmFixture

LIBRARY = tuple(
    FilmFixture(1000 + n, f"Film {n:02d}", release_date=f"{1980 + n}-01-01") for n in range(12)
)
"""A dozen films: enough to fill several bands and to spread across all ten."""


async def account_id(client):
    response = await client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()["id"]


# --- Watching and rating ---


async def mark_watched(client, film, rate="later"):
    response = await client.post(f"/api/films/{film.tmdb_id}/watched", json={"rate": rate})
    assert response.status_code == 200, response.text
    return response.json()


async def picker(client, film, expect=200):
    """Open the band picker: the ten bands with their anchor pools, and nothing else."""
    response = await client.get(f"/api/placements/{film.tmdb_id}")
    assert response.status_code == expect, response.text
    return response.json()


async def pick(client, film, band, expect=200):
    """Tap a band, which is the whole of rating a film."""
    response = await client.post(f"/api/placements/{film.tmdb_id}/band", json={"band": band})
    assert response.status_code == expect, response.text
    return response.json()


async def rate(client, film, band, expect=200):
    """Watch a film and rate it: mark watched, open the picker, tap a band."""
    await mark_watched(client, film, "now")
    await picker(client, film)
    return await pick(client, film, band, expect)


async def re_rate(client, film, band, expect=200):
    """Run the picker again on a rated film, from its page or from a rewatch."""
    await picker(client, film)
    return await pick(client, film, band, expect)


async def abandon(client, film):
    """Open the picker and walk away, which is the whole of abandoning it."""
    await mark_watched(client, film, "now")
    return await picker(client, film)


async def build_ordering(client, films, band=4.0):
    """Rate a run of films into one band, in the order given.

    Within the band they land in the default order rather than the order rated, which is
    the point: nothing about arrival is a judgment.
    """
    for film in films:
        await rate(client, film, band)


async def scale(client, size=5, bands=(5.0, 4.0, 3.0)):
    """A small library spread across ``bands``, one film per band and then round again.

    Returns the film ids in the order they were rated. Spread rather than stacked so the
    account clears the bands-spanned bar and the trainer has cross-band pairs to read.
    """
    films = LIBRARY[:size]
    for index, film in enumerate(films):
        await rate(client, film, bands[index % len(bands)])
    return [film.tmdb_id for film in films]


# --- Anchors ---


async def mark_anchor(client, film, expect=204):
    """Mark a rated film an anchor: one toggle, from its own page."""
    response = await client.post(f"/api/anchors/{film.tmdb_id}")
    assert response.status_code == expect, response.text


async def retire_anchor(client, film, expect=204):
    """The same toggle, off. Changes nothing else."""
    response = await client.delete(f"/api/anchors/{film.tmdb_id}")
    assert response.status_code == expect, response.text


async def anchors(client):
    response = await client.get("/api/anchors")
    assert response.status_code == 200, response.text
    return response.json()


def pool_for(payload, band):
    """One band's pool as plain ids, most recently marked first."""
    row = next(one for one in payload["bands"] if one["band"] == band)
    return [film["tmdb_id"] for film in row["films"]]


async def anchored(client, band, film):
    """Rate a film into a band and mark it, which is the fresh-account bootstrap."""
    landed = await rate(client, film, band)
    await mark_anchor(client, film)
    return landed


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


# --- Quality tags ---


async def given_tags(db, tmdb_id, *qualities):
    """Say what a film is known for, below the seam: the catalog as a tagging left it.

    The one helper here that does not speak HTTP, because there is no owner-facing way to
    tag a film and there should not be - a tag is an account-independent fact the engine
    buys for itself. Tests about *selection* are about what tags do once they exist, and
    buying them through the provider to find out would make every one of them a test of
    the job queue as well. The stamp goes on with the rows, exactly as the job writes
    them, so a film arranged this way is never re-tagged behind the test's back.
    """
    from anchor import tags

    async with db.sessions() as session:
        await tags.record(session, tmdb_id, qualities)
        await session.commit()


# --- The quality picker and profile constraints ---


async def qualities(client):
    """The picker as the owner meets it: their list, what is ticked, and what Anchor guessed."""
    response = await client.get("/api/profile/qualities")
    assert response.status_code == 200, response.text
    return response.json()


async def pick_qualities(client, names, expect=200):
    """Answer the picker with exactly these qualities by name; anything else is unticked.

    Replace rather than add, because that is what a multi-select is: the owner's answer
    is the whole set they left ticked, and unticking is how a selection is taken back.
    """
    listed = {entry["name"]: entry["id"] for entry in (await qualities(client))["qualities"]}
    missing = [name for name in names if name not in listed]
    assert not missing, missing
    response = await client.put(
        "/api/profile/qualities", json={"quality_ids": [listed[name] for name in names]}
    )
    assert response.status_code == expect, response.text
    return response.json() if expect == 200 else None


async def add_quality(client, name, expect=200):
    """The picker's free text: a custom quality joins the account's list."""
    response = await client.post("/api/profile/qualities", json={"name": name})
    assert response.status_code == expect, response.text
    return response.json() if expect == 200 else None


async def thumb_down(client, claim, expect=200):
    """Correct the prose profile: the claim is wrong about them, and stays recorded as such."""
    response = await client.post("/api/profile/constraints", json={"claim": claim})
    assert response.status_code == expect, response.text
    return response.json() if expect == 200 else None


async def lift_correction(client, constraint_id, expect=204):
    """Take a correction back. The row is lifted, never deleted."""
    response = await client.delete(f"/api/profile/constraints/{constraint_id}")
    assert response.status_code == expect, response.text


async def corrections(client):
    """The prose corrections still standing, as the Profile screen carries them."""
    return (await profile(client))["corrections"]


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


async def seen_discovery(client, expect=204):
    """Arriving at Discovery, which is what clears its dot."""
    response = await client.delete("/api/unlocks/discovery")
    assert response.status_code == expect, response.text


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
    """The wall as plain ids per band, best band first and rank order inside each."""
    return {row["band"]: [film["tmdb_id"] for film in row["films"]] for row in payload["rows"]}


def listed_of(payload):
    """The films the screen showed, whichever shape it used: the wall, or a flat sort."""
    return (
        payload["films"]
        if payload["films"] is not None
        else [film for row in payload["rows"] for film in row["films"]]
    )


def bands_of(payload):
    """Every rated film's band, however the screen happened to be sorted."""
    return {film["tmdb_id"]: film["band"] for film in listed_of(payload)}


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


# --- The sync list ---


async def sync_list(client):
    response = await client.get("/api/sync")
    assert response.status_code == 200, response.text
    return response.json()


async def mark_synced(client, film, expect=204):
    """The owner saying they have carried this one film over to Letterboxd by hand."""
    response = await client.post(f"/api/sync/{film.tmdb_id}")
    assert response.status_code == expect, response.text


async def mark_all_synced(client, expect=204):
    response = await client.post("/api/sync/all")
    assert response.status_code == expect, response.text


def synced_pairs(payload, section="changed"):
    """One section as {film: (what Letterboxd holds, what Anchor holds)}."""
    return {row["tmdb_id"]: (row["synced"], row["band"]) for row in payload[section]}


# --- The warmup ---


async def warmup(client):
    response = await client.get("/api/warmup")
    assert response.status_code == 200, response.text
    return response.json()


async def enter_warmup(client):
    response = await client.post("/api/warmup/enter")
    assert response.status_code == 200, response.text
    return response.json()


async def skip_warmup(client, mark, band=None, expect=200):
    response = await client.post("/api/warmup/skip", json={"mark": mark, "band": band})
    assert response.status_code == expect, response.text
    return response.json()


async def dismiss_warmup(client):
    response = await client.post("/api/warmup/dismiss")
    assert response.status_code == 200, response.text
    return response.json()


async def browse(client, kind, expect=200):
    response = await client.get("/api/films/browse", params={"kind": kind})
    assert response.status_code == expect, response.text
    return response.json()


def prompt_for(phase, band):
    """One band's mark prompt, from whichever of the two lists it lives in."""
    return next(one for one in (*phase["prompts"], *phase["continuation"]) if one["band"] == band)
