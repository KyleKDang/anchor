"""The v1 feature pipeline: a film's symbolic TMDB facts as a vector the scorer can read.

Symbolic TMDB only, by decision (ADR 0004): genres, director, top cast, idf-weighted
keywords, and the vote and popularity priors. Embeddings are a later experiment under
the no-training provider rule (ADR 0003), and nothing here ever looks at what the owner
did - the facts are about the film, and the *taste* lives entirely in the weights the
trainer learns over them.

Three ideas carry the module:

*The vocabulary is the owner's own library.* There is no global feature space: the
columns are learned from the films this account has rated, so a director the owner
watches ten times is a column and one they have never seen is not a column at all. A
film the owner has never rated is read through that same vocabulary, and the facts it
carries that the vocabulary has no column for simply drop out - which is exactly how the
vector scores unseen films by construction.

*A fact only one film carries is not a pattern.* It would fit that film perfectly and
say nothing about any other, so it is pruned; with a few hundred films, unpruned
directors and keywords alone would outnumber the training pairs several times over.

*The priors are centred on the library, not on TMDB.* A 7.4 average is only meaningful
against the rest of what the owner rates, so both priors are standardised over the
corpus - and the popularity prior is the log of the vote count, which is the popularity
signal the stored bundle actually carries.
"""

import math
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from anchor.models import Film

VOTE_PRIOR = "prior:vote_average"
POPULARITY_PRIOR = "prior:popularity"
PRIORS: tuple[str, ...] = (VOTE_PRIOR, POPULARITY_PRIOR)
"""The two numeric columns every space has, whatever the library holds."""

CAST_DEPTH = 5
"""How far down the billing a name still says something about why a film was liked."""

MIN_FILMS = 2
"""How many films must share a symbol before it earns a column. One film is not a pattern."""


@dataclass(frozen=True)
class FeatureSpace:
    """The vocabulary one account's rated films define, and what a present symbol weighs.

    ``values`` is the number a column takes when the film carries that symbol: 1.0 for a
    plain indicator, and the idf for a keyword, so a keyword half the library shares
    counts for less than one two films share. Keywords alone are idf-weighted because
    they alone arrive as an open-ended pile per film, where a genre or a director is one
    deliberate fact. The prior columns ignore ``values`` and carry their standardised
    value instead.
    """

    columns: tuple[str, ...]
    values: tuple[float, ...]
    centres: tuple[float, ...]
    scales: tuple[float, ...]
    """Where each prior's column sits and how wide it runs, over the library it was learned on."""

    def __len__(self) -> int:
        return len(self.columns)

    def value_of(self, column: str) -> float:
        return self.values[self.columns.index(column)]

    def vector(self, film: Film) -> np.ndarray:
        """One film as a row in this space. Facts with no column here simply drop out."""
        row = np.zeros(len(self.columns))
        index = {column: position for position, column in enumerate(self.columns)}
        for symbol in symbols(film):
            position = index.get(symbol)
            if position is not None:
                row[position] = self.values[position]
        for prior, value in zip(PRIORS, priors(film), strict=True):
            position = index[prior]
            row[position] = (value - self.centres[position]) / self.scales[position]
        return row

    def to_json(self) -> dict[str, list[Any]]:
        """The space as it is stored beside the weights, which are meaningless without it."""
        return {
            "columns": list(self.columns),
            "values": list(self.values),
            "centres": list(self.centres),
            "scales": list(self.scales),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "FeatureSpace":
        return cls(
            columns=tuple(payload["columns"]),
            values=tuple(payload["values"]),
            centres=tuple(payload["centres"]),
            scales=tuple(payload["scales"]),
        )


def symbols(film: Film) -> Iterator[str]:
    """Every symbolic fact about a film, namespaced so two kinds never collide."""
    for genre in film.genres:
        yield f"genre:{genre}"
    for person in film.credits.get("directors", []):
        yield f"director:{person['name']}"
    for person in film.credits.get("cast", [])[:CAST_DEPTH]:
        yield f"cast:{person['name']}"
    for keyword in film.keywords:
        yield f"keyword:{keyword}"


def priors(film: Film) -> tuple[float, float]:
    """The two numeric facts, before standardisation: how well and how widely rated.

    Popularity is the log of the vote count because the count spans four orders of
    magnitude across any real library, and the difference between 100 and 1,000 votes
    means far more than the difference between 100,000 and 100,900.
    """
    return film.vote_average, math.log1p(film.vote_count)


def learn(films: Sequence[Film]) -> FeatureSpace:
    """Build the space this library defines: which symbols earn a column, and what they weigh.

    An empty library defines nothing, priors included: there is no centre to measure a
    vote average against when there are no films to measure it over.
    """
    if not films:
        return FeatureSpace(columns=(), values=(), centres=(), scales=())
    shared = _shared_symbols(films)
    columns = (*shared, *PRIORS)
    values = (
        *(_value(len(films), symbol, shared[symbol]) for symbol in shared),
        *(1.0 for _ in PRIORS),
    )
    measured = [
        _centre_and_scale(column) for column in zip(*(priors(film) for film in films), strict=True)
    ]
    centres = (*(0.0 for _ in shared), *(centre for centre, _ in measured))
    scales = (*(1.0 for _ in shared), *(scale for _, scale in measured))
    return FeatureSpace(columns=columns, values=values, centres=centres, scales=scales)


def _shared_symbols(films: Sequence[Film]) -> dict[str, int]:
    """Symbols at least :data:`MIN_FILMS` films carry, with how many carry each."""
    counted = Counter(symbol for film in films for symbol in set(symbols(film)))
    return {symbol: count for symbol, count in sorted(counted.items()) if count >= MIN_FILMS}


KEYWORD = "keyword:"


def _value(total: int, symbol: str, carrying: int) -> float:
    """What a present symbol is worth: its idf for a keyword, a plain 1 for everything else."""
    return _idf(total, carrying) if symbol.startswith(KEYWORD) else 1.0


def _idf(total: int, carrying: int) -> float:
    """Smoothed inverse document frequency: rarer in this library, worth more.

    Smoothed (the +1s) so it stays positive and finite for a symbol every film carries,
    which would otherwise weigh exactly nothing and quietly leave the space.
    """
    return math.log((1 + total) / (1 + carrying)) + 1.0


def _centre_and_scale(column: Iterable[float]) -> tuple[float, float]:
    """A prior's mean and spread. A library that agrees on a value carries no signal in it."""
    values = np.array(list(column))
    spread = float(values.std())
    return float(values.mean()), spread or 1.0
