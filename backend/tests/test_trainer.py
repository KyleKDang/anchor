"""The weight-vector trainer, tested directly: the other half of the below-API seam.

What is pinned here is what taste-profile.md fixes about pair extraction - cross-band
pairs at full weight, within-band pairs weighted by distance so neighbours train as
near-equals, explicit band comparisons kept only where the ordering still agrees, a
budget per film, sampling seedable - and the one quality bar that matters: held-out
accuracy clearly beats chance on a taste the test itself invented. The particular numbers
a fit lands on are never asserted, and neither is a clock: the scale a retrain must
survive is pinned as an allocation bound.
"""

import random
import tracemalloc

import library as library_module
from anchor import features, trainer
from anchor.ordering import Ordering, Placed
from anchor.trainer import Answered
from library import library, taste

BANDS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)


def ordering(**rows):
    """An ordering from ``b5_0=[...]`` style keywords: the band, then its films in rank order."""
    return Ordering(
        rows={
            _band(name): tuple(
                Placed(film_id=film_id, band=_band(name), rank=rank, anchored=False)
                for rank, film_id in enumerate(films, start=1)
            )
            for name, films in rows.items()
        }
    )


def _band(name):
    return float(name.removeprefix("b").replace("_", "."))


def stack(*bands):
    """One film per band, best first: the simplest ordering with cross-band pairs in it."""
    return Ordering(
        rows={
            band: (Placed(film_id=film_id, band=band, rank=1, anchored=False),)
            for band, film_id in zip(BANDS, bands, strict=False)
        }
    )


def directed(extracted):
    """Every extracted pair as (better, worse), which is what the spec speaks about."""
    return {(pair.a, pair.b) for pair in extracted}


def weights_of(extracted):
    return {(pair.a, pair.b): pair.weight for pair in extracted}


NO_LONG_RANGE = trainer.Sampling(long_range_per_film=0)
"""Adjacent bands and within-band pairs only, so a test is not reading sampled noise."""

EVERY_PAIR = trainer.Sampling(
    within_per_film=10**6, adjacent_per_film=10**6, long_range_per_film=10**6
)
"""No budget at all: every pair a seeded library implies, as the worker once saw them."""


# --- Across bands ---


def test_a_cross_band_pair_points_from_the_better_band():
    extracted = trainer.extract(stack(1, 2, 3), seed=1, sampling=NO_LONG_RANGE)

    assert (1, 2) in directed(extracted)
    assert (2, 3) in directed(extracted)
    assert (2, 1) not in directed(extracted)


def test_every_cross_band_pair_carries_full_weight():
    """Across bands, order is a judgment (ADR 0013)."""
    extracted = trainer.extract(stack(1, 2, 3), seed=1, sampling=NO_LONG_RANGE)

    assert all(pair.weight == 1.0 for pair in extracted if not pair.within_band)


def test_long_range_pairs_reach_past_the_neighbouring_bands():
    """Adjacency says which way; only a distant band says by how far."""
    ranked = ordering(**{f"b{band}".replace(".", "_"): [index] for index, band in enumerate(BANDS)})

    extracted = trainer.extract(ranked, seed=1, sampling=trainer.Sampling(long_range_per_film=3))

    assert any(abs(pair.a - pair.b) > 1 for pair in extracted)
    assert all(pair.a < pair.b for pair in extracted)


def test_the_sampling_is_seedable():
    ranked = ordering(b5_0=list(range(12)), b3_0=list(range(12, 24)), b1_0=list(range(24, 36)))

    first = directed(trainer.extract(ranked, seed=4))
    again = directed(trainer.extract(ranked, seed=4))
    other = directed(trainer.extract(ranked, seed=99))

    assert first == again
    assert first != other


# --- Within a band ---


def test_within_a_band_neighbours_train_as_near_equals():
    """One rank apart is, to the engine, very nearly the same film."""
    row = list(range(100))

    extracted = trainer.extract(ordering(b4_0=row), seed=1, sampling=EVERY_PAIR)

    weights = weights_of(extracted)
    assert weights[(0, 1)] < 0.05, weights[(0, 1)]


def test_within_a_band_the_top_against_the_bottom_is_a_judgment():
    row = list(range(100))

    extracted = trainer.extract(ordering(b4_0=row), seed=1, sampling=EVERY_PAIR)

    weights = weights_of(extracted)
    assert weights[(0, 99)] == 1.0
    assert weights[(0, 50)] > weights[(0, 10)] > weights[(0, 1)]


def test_a_within_band_pair_is_marked_as_a_range():
    extracted = trainer.extract(ordering(b4_0=[1, 2, 3]), seed=1, sampling=EVERY_PAIR)

    assert all(pair.within_band for pair in extracted)


def test_within_a_band_the_better_rank_comes_first():
    extracted = trainer.extract(ordering(b4_0=[1, 2, 3]), seed=1, sampling=EVERY_PAIR)

    assert directed(extracted) == {(1, 2), (1, 3), (2, 3)}


# --- Explicit band comparisons ---


def test_an_explicit_answer_outweighs_an_implied_pair():
    """The owner judged one of these two; the other the ordering merely implies."""
    ranked = stack(1, 2, 3)

    extracted = trainer.extract(
        ranked, seed=1, explicit=[Answered(better=1, worse=2)], sampling=NO_LONG_RANGE
    )

    weights = weights_of(extracted)
    assert weights[(1, 2)] > weights[(2, 3)]
    assert {(pair.a, pair.b) for pair in extracted if pair.explicit} == {(1, 2)}


def test_an_answer_the_ordering_has_moved_past_is_dropped():
    """A later move wins: the ordering is primary and the log is evidence (ADR 0013)."""
    ranked = stack(1, 2)

    extracted = trainer.extract(
        ranked, seed=1, explicit=[Answered(better=2, worse=1)], sampling=NO_LONG_RANGE
    )

    assert not any(pair.explicit for pair in extracted)
    assert weights_of(extracted)[(1, 2)] == 1.0


def test_an_answer_whose_films_share_a_band_still_stands():
    """Within-band order is a range, not a verdict, so it cannot contradict an answer."""
    ranked = ordering(b4_0=[1, 2])

    extracted = trainer.extract(
        ranked, seed=1, explicit=[Answered(better=2, worse=1)], sampling=EVERY_PAIR
    )

    assert [pair.explicit for pair in extracted] == [True]


def test_an_answered_pair_the_sampling_would_have_missed_is_trained_on_anyway():
    """The owner's own answers are the best evidence there is; none is left to luck.

    A budget of four opponents per film drops most pairs of a real library, so an answer
    that has to wait for the sampler to happen upon it is an answer usually thrown away -
    and the held-out slice is made of exactly these (evaluation.md).
    """
    ranked = ordering(b5_0=list(range(40)), b1_0=list(range(40, 80)))

    extracted = trainer.extract(ranked, seed=1, explicit=[Answered(better=0, worse=79)])

    assert (0, 79) in directed(extracted)
    assert [(pair.a, pair.b) for pair in extracted if pair.explicit] == [(0, 79)]


def test_an_answered_within_band_pair_is_still_read_as_a_range():
    """It carries the explicit weight, and it is still a pair inside one band.

    Which matters for the held-out slice: an answered pair is eligible whichever band its
    films sit in, and the flag is what says so.
    """
    ranked = ordering(b4_0=list(range(40)))

    extracted = trainer.extract(ranked, seed=1, explicit=[Answered(better=0, worse=39)])

    [answered] = [pair for pair in extracted if pair.explicit]
    assert (answered.a, answered.b) == (0, 39)
    assert answered.within_band is True


def test_an_answered_pair_naming_a_film_no_longer_rated_is_left_out():
    extracted = trainer.extract(
        stack(1, 2), seed=1, explicit=[Answered(better=1, worse=404)], sampling=NO_LONG_RANGE
    )

    assert directed(extracted) == {(1, 2)}
    assert not any(pair.explicit for pair in extracted)


def test_an_ordering_too_short_to_have_a_pair_yields_none():
    assert trainer.extract(ordering(b4_0=[1]), seed=1) == []
    assert trainer.extract(ordering(), seed=1) == []


# --- The budget ---


def test_a_big_band_pairs_each_film_a_budgeted_number_of_times():
    """A band of hundreds costs hundreds of pairs, never the square of itself (#59).

    Two hundred films in one half-star band would otherwise imply twenty thousand
    within-band pairs and forty thousand more against the band below, all of them saying
    the same two things. Each film instead draws a budget, so the pair count follows the
    library while the ordering is still captured: every film stands above some of the
    band below and below some of the band above.
    """
    upper, lower = list(range(200)), list(range(200, 400))
    budget = trainer.SAMPLING

    extracted = trainer.extract(ordering(b4_0=upper, b3_5=lower), seed=1, sampling=NO_LONG_RANGE)

    within = [pair for pair in extracted if pair.within_band]
    cross = [pair for pair in extracted if not pair.within_band]
    assert len(within) <= 400 * budget.within_per_film
    assert len(cross) <= 400 * budget.adjacent_per_film
    for film in upper:
        assert sum(pair.a == film for pair in cross) >= budget.adjacent_per_film
    for film in lower:
        assert sum(pair.b == film for pair in cross) >= budget.adjacent_per_film


def test_a_band_that_fits_the_budget_trains_on_every_pair_inside_it():
    budget = trainer.SAMPLING
    row = list(range(budget.within_per_film + 1))

    extracted = trainer.extract(ordering(b4_0=row), seed=1, sampling=NO_LONG_RANGE)

    assert directed(extracted) == {(a, b) for index, a in enumerate(row) for b in row[index + 1 :]}


# --- The held-out slice ---


def test_the_held_out_slice_is_cross_band_pairs_and_the_owner_s_own_answers():
    """Within-band pairs are excluded: the ordering calls them a range, not a verdict."""
    ranked = ordering(b5_0=list(range(20)), b3_0=list(range(20, 40)))

    extracted = trainer.extract(ranked, seed=3, explicit=[Answered(better=0, worse=1)])
    held_out, _ = trainer.hold_out(extracted, share=0.5, seed=3)

    assert held_out
    assert all(pair.explicit or not pair.within_band for pair in held_out)


def test_holding_out_keeps_the_two_slices_disjoint_and_whole():
    ranked = ordering(b5_0=list(range(20)), b3_0=list(range(20, 40)))
    extracted = trainer.extract(ranked, seed=3)

    held_out, training = trainer.hold_out(extracted, share=0.25, seed=3)

    assert held_out and training
    assert len(held_out) + len(training) == len(extracted)
    assert not {id(pair) for pair in held_out} & {id(pair) for pair in training}


def test_nothing_is_held_out_where_the_ordering_offers_nothing_eligible():
    """A one-band account has no cross-band pair and no answer: no accuracy, honestly."""
    extracted = trainer.extract(ordering(b4_0=[1, 2, 3]), seed=3)

    held_out, training = trainer.hold_out(extracted, share=0.5, seed=3)

    assert held_out == []
    assert training == extracted


def test_accuracy_is_unanswerable_where_nothing_was_held_back():
    rows = {film.tmdb_id: film for film in library(4)}
    space = features.learn(list(rows.values()))

    weights = trainer.fit(trainer.design([], space, rows))
    assert trainer.accuracy(weights, trainer.design([], space, rows)) is None


# --- Quality ---


def banded(ranked):
    """Spread a taste-ordered library across the ten bands, best band first.

    A taste is a total order and the ordering is ten rows, so the rows are cut out of it
    in order: the best tenth is the 5.0 row, and so on down. That is what an owner who
    rated their library honestly would have.
    """
    size = max(1, len(ranked) // len(BANDS))
    rows = {}
    for index, band in enumerate(BANDS):
        cut = (
            ranked[index * size : (index + 1) * size]
            if index < len(BANDS) - 1
            else ranked[index * size :]
        )
        if cut:
            rows[band] = tuple(
                Placed(film_id=film.tmdb_id, band=band, rank=rank, anchored=False)
                for rank, film in enumerate(cut, start=1)
            )
    return Ordering(rows=rows)


def test_held_out_accuracy_clearly_beats_chance_on_a_synthetic_taste():
    """The bar the whole scorer exists to clear, on an ordering the test itself invented.

    A hidden per-symbol taste ranks 150 films into ten bands; the trainer sees only the
    resulting rows, with a quarter of the eligible pairs held back. Recovering the
    held-back ones well above a coin flip is what "the scorer works" means at this seam
    (evaluation.md).
    """
    ranked = taste(library(150))
    rows = {film.tmdb_id: film for film in ranked}
    line = banded(ranked)

    extracted = trainer.extract(line, seed=3)
    held_out, training = trainer.hold_out(extracted, share=0.25, seed=3)
    space = features.learn(list(rows.values()))
    weights = trainer.fit(trainer.design(training, space, rows))

    accuracy = trainer.accuracy(weights, trainer.design(held_out, space, rows))
    assert accuracy is not None and accuracy > 0.72, accuracy


def test_a_taste_with_no_signal_in_it_lands_near_chance():
    """The metric has to be able to say "this is not working", or it says nothing at all."""
    films = library(150)
    shuffled = list(films)
    random.Random(5).shuffle(shuffled)
    rows = {film.tmdb_id: film for film in films}
    line = banded(shuffled)

    extracted = trainer.extract(line, seed=3)
    held_out, training = trainer.hold_out(extracted, share=0.25, seed=3)
    space = features.learn(films)
    weights = trainer.fit(trainer.design(training, space, rows))

    accuracy = trainer.accuracy(weights, trainer.design(held_out, space, rows))
    assert accuracy is not None and accuracy < 0.68, accuracy


def test_a_library_separated_by_one_feature_still_learns_it():
    """The step size is read off the data, and the reading has to survive a plain library.

    Every film here is alike but for its genre, which is exactly the shape that defeats a
    curvature probe started from the all-ones vector: each film sums to the same value, so
    every pair difference reads as zero and the fit is handed a step a thousand times too
    long. The signal could hardly be simpler - westerns above horrors, every time - and a
    scorer that cannot recover it is not a scorer.
    """
    plain = [library_film(9000 + index, "Western" if index < 3 else "Horror") for index in range(6)]
    rows = {film.tmdb_id: film for film in plain}
    line = ordering(b5_0=[9000, 9001, 9002], b2_0=[9003, 9004, 9005])

    space = features.learn(plain)
    weights = trainer.fit(trainer.design(trainer.extract(line, seed=1), space, rows))

    assert trainer.score(weights, space, rows[9000]) > trainer.score(weights, space, rows[9005])


def library_film(tmdb_id, genre):
    """One film of a library alike in everything but genre, and flat in both priors."""
    return library_module.film(
        tmdb_id, genres=(genre,), directors=("D",), cast=("A", "B"), keywords=("k",)
    )


def test_a_film_is_vectorised_once_however_many_pairs_it_appears_in():
    """What keeps a retrain in milliseconds, pinned as a fact rather than as a stopwatch.

    ADR 0004's premise is that the fit is cheap enough to run on every ordering change,
    and the way to lose that is to vectorise per appearance rather than per film - at
    library scale each film sits in a dozen pairs, which is a fortyfold difference. A
    wall-clock assertion would be the one non-deterministic thing in a seeded suite
    (testing.md), so what is asserted is the property the clock was standing in for.
    """
    ranked = taste(library(60))
    rows = {film.tmdb_id: film for film in ranked}
    extracted = trainer.extract(banded(ranked), seed=3)
    space = features.learn(list(rows.values()))
    counted = _CountingSpace(space)

    trainer.design(extracted, counted, rows)

    appearances = 2 * len(extracted)
    assert counted.calls == len({film for pair in extracted for film in (pair.a, pair.b)})
    assert counted.calls < appearances / 5, (counted.calls, appearances)


class _CountingSpace:
    """A feature space that records how often it was asked to vectorise a film."""

    def __init__(self, space):
        self._space = space
        self.calls = 0

    def __len__(self):
        return len(self._space)

    def vector(self, film):
        self.calls += 1
        return self._space.vector(film)


# --- Scale ---


LIVE_BANDS = (129, 125, 122, 81, 60, 37, 22, 15, 5, 1)
"""The band sizes one real 597-film Letterboxd import seeded (#59), best band first."""


def seed_import(ranked):
    """The ordering a seed import leaves: one full band row per half-star value."""
    films = iter(ranked)
    return Ordering(
        rows={
            band: tuple(
                Placed(film_id=next(films).tmdb_id, band=band, rank=rank, anchored=False)
                for rank in range(1, size + 1)
            )
            for band, size in zip(BANDS, LIVE_BANDS, strict=True)
        }
    )


def wide_library(size):
    """A library whose feature space is production-wide: a keyword vocabulary in the
    thousands, most of it shared by just enough films to earn a column."""
    pool = tuple(f"keyword {n:04d}" for n in range(1500))
    return library(size, keyword_pool=pool, keywords_per_film=12)


def test_a_seed_shaped_library_retrains_without_a_pair_by_feature_matrix():
    """What keeps the retrain inside the worker's memory, pinned as an allocation bound.

    Unbudgeted, a seed import's ten full band rows imply some eighty thousand training
    rows against a feature space in the low thousands. A row-per-pair matrix of that is
    over a gigabyte, and on the live box it killed the worker (#59).

    The bound is what the fit is allowed to hold: the film-by-feature matrix, twice over
    for working copies, plus a few machine words per pair. Nothing proportional to
    *pairs times features* fits inside it, which is the property the kill was missing.
    """
    ranked = taste(wide_library(597))
    rows = {film.tmdb_id: film for film in ranked}
    extracted = trainer.extract(seed_import(ranked), seed=3, sampling=EVERY_PAIR)
    space = features.learn(ranked)
    assert len(extracted) > 50_000 and len(space) > 1_000, (len(extracted), len(space))

    tracemalloc.start()
    try:
        trainer.fit(trainer.design(extracted, space, rows))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    film_matrix = len(rows) * len(space) * 8
    assert peak < 2 * film_matrix + 100 * len(extracted), (peak, film_matrix, len(extracted))


def test_the_budget_keeps_a_seed_shaped_library_to_a_library_sized_pair_count():
    """The default sampling is what makes the bound above academic rather than load-bearing."""
    ranked = taste(wide_library(597))

    extracted = trainer.extract(seed_import(ranked), seed=3)

    assert len(extracted) < 20 * len(ranked), len(extracted)
