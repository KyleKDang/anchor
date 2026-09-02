"""The v1 feature pipeline, tested directly: the one seam that sits below the API.

Scorer quality is not meaningfully an API behavior (testing.md), so the pipeline is
exercised on known inputs here rather than through a flow. What is pinned is what the
spec fixes - symbolic TMDB facts only, keywords idf-weighted, vote and popularity
priors - and never the particular numbers a fit happens to land on.
"""

import math

from anchor import features
from library import film

CORPUS = [
    film(
        1,
        genres=("Drama",),
        directors=("Fincher",),
        cast=("Norton",),
        keywords=("heist", "noir"),
        vote=8.0,
        votes=9000,
    ),
    film(
        2,
        genres=("Drama",),
        directors=("Fincher",),
        cast=("Pitt",),
        keywords=("heist",),
        vote=7.0,
        votes=1000,
    ),
    film(
        3,
        genres=("Comedy",),
        directors=("Wilder",),
        cast=("Pitt",),
        keywords=("heist",),
        vote=6.0,
        votes=100,
    ),
]


def test_the_vocabulary_is_symbolic_tmdb_facts_and_the_two_priors():
    space = features.learn(CORPUS)

    assert set(space.columns) == {
        "genre:Drama",
        "director:Fincher",
        "cast:Pitt",
        "keyword:heist",
        features.VOTE_PRIOR,
        features.POPULARITY_PRIOR,
    }


def test_a_fact_only_one_film_carries_is_left_out():
    """One film is not a pattern: a column nothing else shares can only memorise it."""
    space = features.learn(CORPUS)

    assert "genre:Comedy" not in space.columns
    assert "cast:Norton" not in space.columns
    assert "keyword:noir" not in space.columns


def test_a_film_carries_exactly_its_own_facts():
    """And only its own: a fact the film lacks, or the vocabulary pruned, stays at zero."""
    space = features.learn(CORPUS)
    carried = _symbolic(space, CORPUS[0])

    assert set(carried) == {"genre:Drama", "director:Fincher", "keyword:heist"}
    assert carried["genre:Drama"] == carried["director:Fincher"] == 1.0


def test_a_keyword_everything_shares_weighs_less_than_a_rarer_one():
    corpus = [
        film(n, genres=("Drama",), keywords=("heist", "noir") if n < 2 else ("heist",))
        for n in range(4)
    ]
    space = features.learn(corpus)

    common = space.value_of("keyword:heist")
    rarer = space.value_of("keyword:noir")
    assert 0 < common < rarer


def test_the_priors_are_centred_on_the_owner_own_library():
    """A 7.4 means nothing absolute; what counts is how it sits against the rest."""
    corpus = [film(n, genres=("Drama",), vote=6.0 + n, votes=10**n) for n in range(5)]
    space = features.learn(corpus)

    votes = [space.vector(one)[space.columns.index(features.VOTE_PRIOR)] for one in corpus]
    assert math.isclose(sum(votes), 0.0, abs_tol=1e-9)
    assert votes == sorted(votes)


def test_a_corpus_too_thin_to_share_anything_still_yields_the_priors():
    """A one-film account has no patterns, and the pipeline says so rather than failing."""
    space = features.learn([film(1, genres=("Drama",))])

    assert space.columns == (features.VOTE_PRIOR, features.POPULARITY_PRIOR)
    assert len(space.vector(film(1, genres=("Drama",)))) == 2


def test_an_unseen_film_is_read_through_the_vocabulary_it_shares():
    """Scoring a never-rated film is the vector's whole job: unknown facts drop out."""
    space = features.learn(CORPUS)
    unseen = film(99, genres=("Drama", "Horror"), directors=("Someone New",))

    carried = _symbolic(space, unseen)

    assert set(carried) == {"genre:Drama"}


def _symbolic(space, film):
    """A film's row, minus the two priors: the facts it carries and what each weighs."""
    return {
        column: value
        for column, value in zip(space.columns, space.vector(film), strict=True)
        if value != 0 and column not in features.PRIORS
    }
