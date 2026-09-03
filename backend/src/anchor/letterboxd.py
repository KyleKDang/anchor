"""The Letterboxd side: reading an account export, and the one page scrape it sanctions.

Anchor has no live Letterboxd connection. An export zip is the only data that ever
crosses over, and this module is the whole of Anchor's knowledge of its shape.

*Five files matter; everything else is discarded unread.* A real export carries sixteen
- reviews, comments, likes, and ``deleted/``, ``orphaned/`` folders whose ``diary.csv``
is identical in shape to the one that counts - so members are matched at the archive
root by exact name, never by what a path happens to end with. profile.csv is the sharp
case: it is read for its Favorite Films column and no other, because every other column
is PII with no product use (onboarding-and-import.md).

*A normalized title is a comparison key, not a title.* Letterboxd and TMDB write the
same film differently - a non-breaking space after an en dash, a middle dot in WALL·E,
an accent in Léon - so :func:`normalized` folds both sides down to letters, digits, and
single spaces before they are compared. That is broader than the three foldings the
spec names, and deliberately: punctuation never distinguishes two films, and the
auto-accept rules the matcher applies on top of this key require a year or a popularity
landslide anyway, so being forgiving here cannot on its own accept a wrong film.

*The boxd.it scrape is a rescue, never a pipeline.* :class:`Letterboxd` resolves one
short link at a time by following it to the film page and reading the TMDB id out of
undocumented markup. It is offered per row, throttled, and expected to fail; nothing in
the import depends on it (ADR 0003's posture, and the research note behind it).
"""

import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from anchor.models import ImportRowKind

RATINGS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
"""The ten values Letterboxd's scale holds, which map 1:1 onto Anchor's bands."""

MAX_ROWS = 20_000
"""A ceiling on how much one archive may claim to be; the real one held 592 ratings."""


class NotAnExport(Exception):
    """The upload is not a readable Letterboxd export."""


@dataclass(frozen=True)
class ExportRow:
    """One exported line that matters, parsed and nothing more.

    ``occurred_at`` is when the row's own event happened - watched, added, or rated.
    Letterboxd exports dates in New Zealand time, so it can sit a day off the owner's
    own calendar; that is accepted rather than corrected, because nothing in Anchor is
    denominated in calendar time and only differences between watches ever matter.
    """

    kind: ImportRowKind
    name: str
    year: int | None
    uri: str | None
    rating: float | None
    occurred_at: datetime | None
    rewatch: bool


# --- Reading the archive ---


def read_export(data: bytes) -> list[ExportRow]:
    """Every row of the five files that matter, in the order the archive holds them."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise NotAnExport("That file is not a zip archive.") from error

    members = {name for name in archive.namelist()}
    readers = (
        ("ratings.csv", _ratings),
        ("watchlist.csv", _watchlist),
        ("watched.csv", _watched),
        ("diary.csv", _diary),
        ("profile.csv", _favorites),
    )
    if not any(name in members for name, _ in readers):
        raise NotAnExport("That zip does not look like a Letterboxd export.")

    rows: list[ExportRow] = []
    for name, reader in readers:
        if name not in members:
            continue  # folders and files are conditional on account content (#16)
        rows.extend(reader(_rows(archive, name)))
        if len(rows) > MAX_ROWS:
            raise NotAnExport("That export is larger than Anchor will import.")
    return rows


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    """One member as dict rows. Read through ``csv``, so a quoted comma survives."""
    try:
        body = archive.read(name).decode("utf-8-sig")
    except (KeyError, UnicodeDecodeError) as error:
        raise NotAnExport(f"{name} could not be read.") from error
    yield from csv.DictReader(io.StringIO(body, newline=""))


def _ratings(rows: Iterable[dict[str, str]]) -> Iterator[ExportRow]:
    for row in rows:
        rating = _rating(row.get("Rating"))
        if rating is not None:
            yield _row(ImportRowKind.rating, row, rating=rating)


def _watchlist(rows: Iterable[dict[str, str]]) -> Iterator[ExportRow]:
    for row in rows:
        yield _row(ImportRowKind.watchlist, row)


def _watched(rows: Iterable[dict[str, str]]) -> Iterator[ExportRow]:
    for row in rows:
        yield _row(ImportRowKind.watched, row)


def _diary(rows: Iterable[dict[str, str]]) -> Iterator[ExportRow]:
    """Diary rows become watch events, so the watched date is the one that counts."""
    for row in rows:
        yield _row(
            ImportRowKind.diary,
            row,
            when=_date(row.get("Watched Date")) or _date(row.get("Date")),
            rewatch=(row.get("Rewatch") or "").strip().casefold() == "yes",
        )


def _favorites(rows: Iterable[dict[str, str]]) -> Iterator[ExportRow]:
    """The Favorite Films column, and not one other field of profile.csv.

    The column is a comma-separated list of names with no years and no URIs, so a
    favourite whose own title contains a comma splits into two names that will not
    match. That is left alone rather than guessed at: favourites only boost a band's
    anchor candidates during the warmup, so a lost one costs an ordering hint.
    """
    for row in rows:
        for name in (row.get("Favorite Films") or "").split(","):
            if name.strip():
                yield ExportRow(
                    kind=ImportRowKind.profile_favorite,
                    name=name.strip(),
                    year=None,
                    uri=None,
                    rating=None,
                    occurred_at=None,
                    rewatch=False,
                )


def _row(
    kind: ImportRowKind,
    row: dict[str, str],
    *,
    rating: float | None = None,
    when: datetime | None = None,
    rewatch: bool = False,
) -> ExportRow:
    return ExportRow(
        kind=kind,
        name=(row.get("Name") or "").strip(),
        year=_year(row.get("Year")),
        uri=(row.get("Letterboxd URI") or "").strip() or None,
        rating=rating,
        occurred_at=when if when is not None else _date(row.get("Date")),
        rewatch=rewatch,
    )


def _year(value: str | None) -> int | None:
    """A missing year degrades the search sharply but is not a broken row."""
    text = (value or "").strip()
    return int(text) if text.isdigit() and len(text) == 4 else None


def _rating(value: str | None) -> float | None:
    """Whole stars serialise without a decimal, so this parses rather than pattern-matches."""
    try:
        rating = float((value or "").strip())
    except ValueError:
        return None
    return rating if rating in RATINGS else None


def _date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


# --- Comparing titles ---

_NOT_WORD = re.compile(r"[^0-9a-z]+")


def normalized(title: str) -> str:
    """A title folded to the key two spellings of the same film have in common.

    Compatibility-decomposed (which is what turns a non-breaking space into a space),
    stripped of the combining marks accents are made of, lowercased, and then reduced to
    runs of letters and digits separated by single spaces - so an en dash, a hyphen and
    a middle dot all read the same, and ``Monsters, Inc.`` meets ``Monsters Inc``.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NOT_WORD.sub(" ", unaccented.casefold()).strip()


# --- The rescue ---

TMDB_ID = re.compile(rb'data-tmdb-id="(\d+)"')
TMDB_TYPE = re.compile(rb'data-tmdb-type="(\w+)"')

BROWSER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
"""Letterboxd answers 403 to obvious fetchers; this is one request at a time, by hand."""


class RescueFailed(Exception):
    """The link did not resolve to a TMDB movie. Expected, and never fatal."""


class Letterboxd:
    """Resolves one boxd.it short link to a TMDB movie id, or fails and says so.

    Undocumented markup on a page Letterboxd never promised to serve us, so every
    failure mode - a 403, a redirect chain that ends nowhere, a page whose attributes
    have been renamed, an entry that turns out to be a TV series - comes back as the
    same refusal, and the row it was called for stays exactly as it was.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            transport=transport,
            timeout=10.0,
            headers={"User-Agent": BROWSER_AGENT},
        )

    async def tmdb_id(self, uri: str) -> int:
        try:
            response = await self._client.get(uri)
        except httpx.HTTPError as error:
            raise RescueFailed(f"{uri} could not be reached") from error
        if response.is_error:
            raise RescueFailed(f"{uri} answered {response.status_code}")

        kind = TMDB_TYPE.search(response.content)
        found = TMDB_ID.search(response.content)
        if found is None or (kind is not None and kind.group(1) != b"movie"):
            # A TV-side entry is the common one: Letterboxd hosts miniseries and TV
            # movies "for historic reasons", and no film id exists to bind.
            raise RescueFailed(f"{uri} does not name a TMDB movie")
        return int(found.group(1))

    async def aclose(self) -> None:
        await self._client.aclose()
