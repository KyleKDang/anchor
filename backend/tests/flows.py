"""Driving Anchor's flows over the JSON API, in the vocabulary the design uses.

Tests read as what the owner did - mark a film watched, rate it, re-rate it, mark an
anchor - rather than as endpoint calls, so they survive the endpoints moving. Every
driver asserts its own call succeeded, which keeps the failure at the step that actually
broke instead of three assertions later.
"""

from faketmdb import FilmFixture
from invariants import assert_no_rating_keys

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


# --- Narrowing a range ---


async def narrow(client, film, bands, answered=(), verdict=None, expect=200):
    """One step of narrowing: hand back the transcript, get the next question.

    ``answered`` is what the screen is carrying and ``verdict`` the answer just given.
    With no verdict this asks the range where it stands and writes nothing, which is how
    the first question arrives.
    """
    response = await client.post(
        f"/api/placements/{film.tmdb_id}/narrow",
        json={"bands": list(bands), "answered": list(answered), "verdict": verdict},
    )
    assert response.status_code == expect, response.text
    return response.json()


async def answer(client, film, bands, verdicts):
    """Answer a whole run of comparisons in order, and hand back where it left off."""
    step = await narrow(client, film, bands)
    for index, verdict in enumerate(verdicts):
        step = await narrow(client, film, bands, verdicts[:index], verdict)
    return step


async def land(client, film, band, bands=(), answered=(), closer=None, expect=200):
    """End a range: the band the answers settled on, or the boundary film it is closer to."""
    response = await client.post(
        f"/api/placements/{film.tmdb_id}/band",
        json={
            "band": band,
            "bands": list(bands),
            "answered": list(answered),
            "closer": closer,
        },
    )
    assert response.status_code == expect, response.text
    return response.json()


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


# --- Moves ---


async def move(client, film, band, rank, expect=200):
    """Drag a film to a rank in a band on the wall, which is the whole of a move.

    ``rank`` is the place the film holds once it has landed: 1..n inside its own band,
    1..n+1 in another. Every drop saves at once, so one call is one drop.
    """
    response = await client.post(
        f"/api/rated/{film.tmdb_id}/move", json={"band": band, "rank": rank}
    )
    assert response.status_code == expect, response.text
    return response.json()


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


async def thumb_down(client, claim, excludes=None, expect=200):
    """Correct the prose profile: the claim is wrong about them, and stays recorded as such.

    ``excludes`` names the structural footprint where the claim has one - a genre or a
    language the owner has ruled out - which the discovery prefilter then enforces by
    dropping films rather than by asking a regeneration to write around them.
    """
    body = {"claim": claim}
    if excludes is not None:
        body["excludes"] = excludes
    response = await client.post("/api/profile/constraints", json=body)
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


async def answer_criteria(client, card, verdict, expect=200):
    """Answer a card, and hand back the next one in its home - or None when the home is done.

    Not answering is the other half of the flow, and is nothing: dismissing the card and
    leaving the screen are both the absence of this call.
    """
    response = await client.post(f"/api/criteria/{card['id']}", json={"verdict": verdict})
    assert response.status_code == expect, response.text
    return response.json()["next"] if expect == 200 else response.json()


async def dismiss_criteria(client, card, expect=200):
    """Wave a card away. In a session the next one comes; in a run, nothing does."""
    response = await client.post(f"/api/criteria/{card['id']}/dismiss")
    assert response.status_code == expect, response.text
    return response.json()["next"] if expect == 200 else response.json()


async def answer_run(client, card, verdicts="a", limit=200):
    """Answer card after card until the home has nothing left, or ``limit`` cards have been met.

    Hands back every card the owner met, first to last. ``verdicts`` cycles, so one letter
    answers everything the same way; a string of several scripts the sequence.
    """
    met = []
    turn = 0
    while card is not None and turn < limit:
        met.append(card)
        card = await answer_criteria(client, card, verdicts[turn % len(verdicts)])
        turn += 1
    return met


async def open_session(client, film, expect=200):
    """Open the session from a film's page: its first card, or None when nothing is left."""
    response = await client.post(f"/api/criteria/session/{film.tmdb_id}")
    assert response.status_code == expect, response.text
    return response.json()["card"] if expect == 200 else response.json()


async def compared_in_picker(db, account_id, subject, opponent, verdict="a"):
    """Record that the picker set ``subject`` against ``opponent``, below the seam.

    The band picker's range comparisons are their own ticket, so until they exist this is
    the only way a film reaches the ladder's third rung. Written exactly as the picker
    will write it: a band comparison whose subject is the film being rated.
    """
    from anchor.models import (
        ComparisonContext,
        ComparisonKind,
        ComparisonLogEntry,
        ComparisonVerdict,
    )

    async with db.sessions() as session:
        session.add(
            ComparisonLogEntry(
                account_id=account_id,
                kind=ComparisonKind.band_comparison,
                subject_film_id=subject.tmdb_id,
                film_a_id=subject.tmdb_id,
                film_b_id=opponent.tmdb_id,
                verdict=ComparisonVerdict(verdict),
                context=ComparisonContext.placement,
            )
        )
        await session.commit()


def pair_of(card):
    """The two films a card names, as a set of tmdb ids."""
    return {card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"]}


def opponent_of(card, subject):
    """The film a card sets ``subject`` against."""
    (other,) = pair_of(card) - {subject.tmdb_id}
    return other


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


# --- Discovery ---


async def discovery(client, boundary=True, expect=200):
    """Open the Discovery screen. Arriving is what queues a restock; a reload is not."""
    response = await client.get("/api/discovery", params={"boundary": str(boundary).lower()})
    assert response.status_code == expect, response.text
    feed = response.json()
    # Asserted on every read rather than in one test, because this is the invariant the
    # whole screen exists under: the feed is about films the owner has not watched, so no
    # rating-shaped key may appear at all (ADR 0005), and the fit bucket that decided the
    # order is internal - a card carries a sentence and a position and nothing else.
    assert_no_rating_keys(feed, "the discovery feed")
    for card in feed["films"]:
        # On the keys, not on the text: a pitch is a sentence about a film, and one that
        # happened to say "outfit" would trip a substring scan for no reason at all.
        assert "fit" not in card, f"the discovery feed leaked a fit bucket: {card}"
    return feed


async def shelf(client, boundary=True):
    """Just the films on the shelf, in the order the screen shows them."""
    return (await discovery(client, boundary=boundary))["films"]
