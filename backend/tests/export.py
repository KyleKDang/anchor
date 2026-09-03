"""Letterboxd account exports, built in memory, for the seed import's tests.

The headers are the ones verified against a real 592-row export (#16), written through
``csv`` rather than joined by hand, so a comma inside a title is quoted exactly as
Letterboxd quotes it and a naive parser fails here the way it would fail on the real
file.

``extras`` writes the rest of the archive the importer must never read: reviews and
comments at the root, the likes folder, and the deleted and orphaned folders whose
``diary.csv`` looks exactly like the one that does count.
"""

import csv
import io
import zipfile
from dataclasses import dataclass

NAME = "letterboxd-owner-2026-08-02-11-00-utc.zip"

EMAIL = "owner@example.com"
"""profile.csv's PII: nothing in the account realm may ever come to hold it."""


@dataclass(frozen=True)
class Row:
    """One exported line. The defaults are a plain, unremarkable film."""

    name: str
    year: int | None = 2010
    rating: float | None = None
    uri: str | None = None
    date: str = "2024-05-01"
    watched_date: str | None = None
    rewatch: bool = False

    @property
    def year_text(self) -> str:
        return "" if self.year is None else str(self.year)

    @property
    def rating_text(self) -> str:
        """Whole stars serialise without a decimal, exactly as Letterboxd writes them."""
        if self.rating is None:
            return ""
        return str(int(self.rating)) if self.rating == int(self.rating) else str(self.rating)

    @property
    def uri_text(self) -> str:
        return self.uri or f"https://boxd.it/{abs(hash(self.name)) % 100000:05x}"


def export(
    *,
    ratings: tuple[Row, ...] = (),
    watchlist: tuple[Row, ...] = (),
    watched: tuple[Row, ...] = (),
    diary: tuple[Row, ...] = (),
    favorites: tuple[str, ...] = (),
    extras: bool = True,
    omit: tuple[str, ...] = (),
) -> bytes:
    """A zip of the five files that matter, plus whatever else a real export carries."""
    members = {
        "ratings.csv": _csv(
            ("Date", "Name", "Year", "Letterboxd URI", "Rating"),
            [(r.date, r.name, r.year_text, r.uri_text, r.rating_text) for r in ratings],
        ),
        "watchlist.csv": _csv(
            ("Date", "Name", "Year", "Letterboxd URI"),
            [(r.date, r.name, r.year_text, r.uri_text) for r in watchlist],
        ),
        "watched.csv": _csv(
            ("Date", "Name", "Year", "Letterboxd URI"),
            [(r.date, r.name, r.year_text, r.uri_text) for r in watched],
        ),
        "diary.csv": _diary(diary),
        "profile.csv": _profile(favorites),
    }
    if extras:
        members.update(_discarded())
    return _zip({name: body for name, body in members.items() if name not in omit})


def _diary(rows: tuple[Row, ...]) -> str:
    return _csv(
        ("Date", "Name", "Year", "Letterboxd URI", "Rating", "Rewatch", "Tags", "Watched Date"),
        [
            (
                row.date,
                row.name,
                row.year_text,
                row.uri_text,
                row.rating_text,
                "Yes" if row.rewatch else "",
                "",
                row.watched_date or row.date,
            )
            for row in rows
        ],
    )


def _profile(favorites: tuple[str, ...]) -> str:
    """The whole PII header, so a parser reading more than one column is caught here."""
    return _csv(
        (
            "Date Joined",
            "Username",
            "Given Name",
            "Family Name",
            "Email Address",
            "Location",
            "Website",
            "Bio",
            "Pronoun",
            "Favorite Films",
        ),
        [
            (
                "2015-03-01",
                "owner",
                "Given",
                "Family",
                EMAIL,
                "Somewhere",
                "https://example.com",
                "A bio nobody asked for",
                "they/them",
                ", ".join(favorites),
            )
        ],
    )


def _discarded() -> dict[str, str]:
    """Everything else the archive carries. Reading any of it is a bug this catches."""
    unread = Row("Never Read This", 1999)
    return {
        "reviews.csv": _csv(
            (
                "Date",
                "Name",
                "Year",
                "Letterboxd URI",
                "Rating",
                "Rewatch",
                "Review",
                "Tags",
                "Watched Date",
            ),
            [
                (
                    unread.date,
                    unread.name,
                    "1999",
                    unread.uri_text,
                    "5",
                    "",
                    "words",
                    "",
                    unread.date,
                )
            ],
        ),
        "comments.csv": _csv(("Date", "Content", "Comment"), [("2024-01-01", "a list", "a word")]),
        "likes/films.csv": _csv(
            ("Date", "Name", "Year", "Letterboxd URI"),
            [(unread.date, unread.name, "1999", unread.uri_text)],
        ),
        # The two folders whose diary.csv is byte-identical in shape to the real one:
        # anything matching on the file name alone rather than its place in the archive
        # imports deleted and orphaned rows as though the owner had watched them.
        "deleted/diary.csv": _diary((unread,)),
        "orphaned/diary.csv": _diary((unread,)),
    }


def _csv(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()
