"""Films as plain catalog rows, for the two module tests that sit below the API seam.

The pipeline and the trainer read a stored ``Film`` and nothing else, so they can be
exercised on films that were never fetched, stored, or rated by anybody.
"""

import random

from anchor import features
from anchor.models import Film

GENRES = ("Drama", "Comedy", "Horror", "Thriller", "Western", "Romance", "Crime", "Sci-Fi")
DIRECTORS = tuple(f"Director {n:02d}" for n in range(12))
PLAYERS = tuple(f"Player {n:02d}" for n in range(30))
KEYWORDS = tuple(f"keyword {n:02d}" for n in range(25))


def film(tmdb_id, *, genres=(), directors=(), cast=(), keywords=(), vote=7.0, votes=1000):
    return Film(
        tmdb_id=tmdb_id,
        title=f"Film {tmdb_id}",
        release_year=2000,
        overview="",
        genres=list(genres),
        keywords=list(keywords),
        credits={
            "directors": [{"name": name} for name in directors],
            "cast": [{"name": name} for name in cast],
        },
        vote_average=vote,
        vote_count=votes,
    )


def library(size, seed=7):
    """A library of ``size`` films drawn from small shared pools, so facts recur."""
    rng = random.Random(seed)
    return [
        film(
            9000 + n,
            genres=rng.sample(GENRES, 2),
            directors=[rng.choice(DIRECTORS)],
            cast=rng.sample(PLAYERS, 4),
            keywords=rng.sample(KEYWORDS, 5),
            vote=rng.uniform(4.0, 9.0),
            votes=rng.randint(50, 200_000),
        )
        for n in range(size)
    ]


def taste(films, seed=11):
    """A synthetic owner: one hidden weight per symbol, and the ordering it produces.

    Returns the films best-first under that taste. The trainer never sees the weights;
    beating chance on held-out pairs means it recovered the shape of them from the order.
    """
    rng = random.Random(seed)
    hidden = {symbol: rng.gauss(0, 1) for film in films for symbol in features.symbols(film)}
    scored = sorted(
        films,
        key=lambda one: -sum(hidden[symbol] for symbol in features.symbols(one)),
    )
    return scored
