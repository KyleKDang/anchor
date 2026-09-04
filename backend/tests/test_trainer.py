"""The weight-vector trainer, tested directly: the other half of the below-API seam.

What is pinned here is what taste-profile.md fixes about pair extraction - every
adjacent pair, a per-film budget inside a seeded band, sampled long-range pairs, ties as
equality targets, explicit answers outweighing implied ones, provisional placements
down-weighted, sampling seedable - and the one quality bar that matters: held-out
accuracy clearly beats chance on a taste the test itself invented. The particular
numbers a fit lands on are never asserted, and neither is a clock: the scale a retrain
must survive is pinned as an allocation bound.
"""

import random
import tracemalloc
import uuid
from itertools import combinations

import library as library_module
from anchor import features, trainer
from anchor.ordering import Ordering, Slot
from library import library, taste


def ordering(*slots):
    return Ordering(
        tuple(
            Slot(id=uuid.uuid4(), position=index, film_ids=tuple(films))
            for index, films in enumerate(slots)
        )
    )


def answered(ordering):
    """The pairs a real placement flow leaves in the log: every film's own bisection.

    Not the adjacent pairs: a placement narrows by halving, so its answers run from a
    film halfway across the ordering down to the immediate neighbours. Holding out only
    neighbours would measure the one comparison no scorer can make - two films the owner
    themselves could barely separate.
    """
    ids = [slot.film_ids[0] for slot in ordering.slots]
    pairs = set()
    for index, film in enumerate(ids):
        lo, hi = 0, len(ids)
        while lo < hi:
            mid = (lo + hi) // 2
            if ids[mid] != film:
                pairs.add(frozenset({film, ids[mid]}))
            if index <= mid:
                hi = mid
            else:
                lo = mid + 1
    return pairs


def pairs_of(extracted):
    """Every extracted pair as (a, b, target), which is what the spec speaks about."""
    return {(pair.a, pair.b, pair.target) for pair in extracted}


NO_SAMPLING = trainer.Sampling(long_range_per_film=0)
"""Adjacency and ties only, so a test about those is not reading sampled noise."""

EVERY_PAIR = trainer.Sampling(tied_per_film=10**6, adjacent_per_film=10**6)
"""No budget on a tie-group: every pair a seed import implies, as the worker once saw."""


def test_every_adjacent_pair_is_extracted_better_first():
    """Adjacency fully captures the order, so it is the one thing never sampled."""
    extracted = trainer.extract(ordering([1], [2], [3]), seed=1, sampling=NO_SAMPLING)

    assert pairs_of(extracted) == {(1, 2, 1.0), (2, 3, 1.0)}


def test_a_tie_group_trains_as_an_equality_target():
    """The owner said these are the same film to them, which is a judgment, not a gap."""
    extracted = trainer.extract(ordering([1, 2], [3]), seed=1, sampling=NO_SAMPLING)

    assert (1, 2, trainer.TIED) in pairs_of(extracted)


def test_adjacency_reaches_across_a_tie_group():
    """Both members of a slot are above both members of the next: the order says so."""
    extracted = trainer.extract(ordering([1, 2], [3, 4]), seed=1, sampling=NO_SAMPLING)

    assert {(1, 3, 1.0), (1, 4, 1.0), (2, 3, 1.0), (2, 4, 1.0)} <= pairs_of(extracted)


def test_long_range_pairs_teach_magnitude_beyond_the_neighbours():
    """Adjacency says which way; only a distant pair says by how far."""
    ranked = ordering(*([n] for n in range(12)))

    extracted = trainer.extract(ranked, seed=1, sampling=trainer.Sampling(long_range_per_film=3))

    assert any(abs(pair.a - pair.b) > 1 for pair in extracted)
    assert all(pair.a < pair.b for pair in extracted if pair.target == 1.0)


def test_the_sampling_is_seedable():
    ranked = ordering(*([n] for n in range(12)))

    first = pairs_of(trainer.extract(ranked, seed=4))
    again = pairs_of(trainer.extract(ranked, seed=4))
    other = pairs_of(trainer.extract(ranked, seed=99))

    assert first == again
    assert first != other


def test_an_explicit_answer_outweighs_an_implied_pair():
    """The owner judged one of these two; the other the ordering merely implies."""
    ranked = ordering([1], [2], [3])

    extracted = trainer.extract(ranked, seed=1, explicit={frozenset({1, 2})}, sampling=NO_SAMPLING)

    weights = {(pair.a, pair.b): pair.weight for pair in extracted}
    assert weights[(1, 2)] > weights[(2, 3)]


def test_a_provisional_placement_is_down_weighted_until_it_graduates():
    ranked = ordering([1], [2], [3], [4])

    settled = trainer.extract(ranked, seed=1, sampling=NO_SAMPLING)
    unsettled = trainer.extract(ranked, seed=1, provisional={3}, sampling=NO_SAMPLING)

    before = {(pair.a, pair.b): pair.weight for pair in settled}
    after = {(pair.a, pair.b): pair.weight for pair in unsettled}
    assert after[(2, 3)] < before[(2, 3)]
    assert after[(3, 4)] < before[(3, 4)]
    assert after[(1, 2)] == before[(1, 2)]


def test_an_answered_pair_the_sampling_would_have_missed_is_trained_on_anyway():
    """The owner's own answers are the best evidence there is; none is left to luck."""
    ranked = ordering(*([n] for n in range(12)))

    extracted = trainer.extract(ranked, seed=1, explicit={frozenset({0, 9})}, sampling=NO_SAMPLING)

    assert (0, 9, 1.0) in pairs_of(extracted)


def test_an_answered_pair_points_the_way_the_ordering_does():
    """The ordering is the primary state and the log is evidence about it (ADR 0010)."""
    ranked = ordering([5], [3])

    extracted = trainer.extract(ranked, seed=1, explicit={frozenset({3, 5})})

    assert pairs_of(extracted) == {(5, 3, 1.0)}


def test_an_answered_pair_naming_a_film_no_longer_rated_is_left_out():
    extracted = trainer.extract(
        ordering([1], [2]), seed=1, explicit={frozenset({1, 404})}, sampling=NO_SAMPLING
    )

    assert pairs_of(extracted) == {(1, 2, 1.0)}


def test_an_ordering_too_short_to_have_a_pair_yields_none():
    assert trainer.extract(ordering([1]), seed=1) == []
    assert trainer.extract(ordering(), seed=1) == []


def test_a_seeded_band_pairs_each_film_a_budgeted_number_of_times():
    """A band-sized tie-group is paired per film, never per pair.

    Two hundred films seeded into one half-star band would otherwise imply twenty
    thousand tie pairs, and forty thousand more against the band below - all of them
    saying the same two things. Each film instead draws a budget of slot-mates and of
    opponents either side, so the pair count follows the library and the ordering is
    still fully captured: every film ties with some of its band and stands above and
    below some of its neighbours.
    """
    upper, lower = list(range(200)), list(range(200, 400))
    budget = trainer.SAMPLING

    extracted = trainer.extract(ordering(upper, lower), seed=1, sampling=NO_SAMPLING)

    ties = [pair for pair in extracted if pair.target == trainer.TIED]
    cross = [pair for pair in extracted if pair.target == 1.0]
    assert len(ties) <= 400 * budget.tied_per_film
    assert len(cross) <= 400 * budget.adjacent_per_film
    for film in upper:
        assert sum(film in (pair.a, pair.b) for pair in ties) >= budget.tied_per_film
        assert sum(pair.a == film for pair in cross) >= budget.adjacent_per_film
    for film in lower:
        assert sum(pair.b == film for pair in cross) >= budget.adjacent_per_film


def test_a_tie_group_that_fits_the_budget_still_trains_on_every_pair():
    """The budget only bites on a seeded band; a tie the owner made by hand is whole."""
    budget = trainer.SAMPLING
    upper = list(range(budget.tied_per_film + 1))
    lower = list(range(100, 100 + budget.adjacent_per_film))

    extracted = trainer.extract(ordering(upper, lower), seed=1, sampling=NO_SAMPLING)

    ties = {(a, b, trainer.TIED) for slot in (upper, lower) for a, b in combinations(slot, 2)}
    assert pairs_of(extracted) == ties | {(a, b, 1.0) for a in upper for b in lower}


LIVE_BANDS = (129, 125, 122, 81, 60, 37, 22, 15, 5, 1)
"""The tie-group sizes one real 597-film Letterboxd import seeded (#59), best band first."""


def seed_import(ranked):
    """The ordering a seed import leaves: one provisional tie-group per half-star band."""
    films = iter(film.tmdb_id for film in ranked)
    return ordering(*([next(films) for _ in range(size)] for size in LIVE_BANDS))


def wide_library(size):
    """A library whose feature space is production-wide: a keyword vocabulary in the
    thousands, most of it shared by just enough films to earn a column."""
    pool = tuple(f"keyword {n:04d}" for n in range(1500))
    return library(size, keyword_pool=pool, keywords_per_film=12)


# --- Quality ---


def test_held_out_accuracy_clearly_beats_chance_on_a_synthetic_taste():
    """The bar the whole scorer exists to clear, on an ordering the test itself invented.

    A hidden per-symbol taste ranks 150 films; the trainer sees only the resulting order,
    with a fifth of the answers held back. Recovering the held-back ones well above a
    coin flip is what "the scorer works" means at this seam (evaluation.md).

    The bar is deliberately not a high one. Half the held-out pairs are near-neighbours
    the synthetic taste itself barely separates, so the ceiling here is nowhere near 1.0;
    what the pair of tests pins is the gap - this taste lands in the high seventies and
    the signal-free ordering below it stays in the fifties.
    """
    ranked = taste(library(150))
    rows = {film.tmdb_id: film for film in ranked}
    line = ordering(*([film.tmdb_id] for film in ranked))

    extracted = trainer.extract(line, seed=3, explicit=answered(line))
    held_out, training = trainer.hold_out(extracted, share=0.2, seed=3)
    space = features.learn(list(rows.values()))
    weights = trainer.fit(trainer.design(training, space, rows))

    accuracy = trainer.accuracy(weights, trainer.design(held_out, space, rows))
    assert accuracy is not None and accuracy > 0.72, accuracy


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
    line = ordering(*([film.tmdb_id] for film in plain))

    space = features.learn(plain)
    weights = trainer.fit(trainer.design(trainer.extract(line, seed=1), space, rows))

    assert trainer.score(weights, space, rows[9000]) > trainer.score(weights, space, rows[9005])


def library_film(tmdb_id, genre):
    """One film of a library alike in everything but genre, and flat in both priors."""
    return library_module.film(
        tmdb_id, genres=(genre,), directors=("D",), cast=("A", "B"), keywords=("k",)
    )


def test_a_taste_with_no_signal_in_it_lands_near_chance():
    """The metric has to be able to say "this is not working", or it says nothing at all."""
    films = library(150)
    shuffled = list(films)
    random.Random(5).shuffle(shuffled)
    rows = {film.tmdb_id: film for film in films}
    line = ordering(*([film.tmdb_id] for film in shuffled))

    extracted = trainer.extract(line, seed=3, explicit=answered(line))
    held_out, training = trainer.hold_out(extracted, share=0.2, seed=3)
    space = features.learn(films)
    weights = trainer.fit(trainer.design(training, space, rows))

    accuracy = trainer.accuracy(weights, trainer.design(held_out, space, rows))
    assert accuracy is not None and accuracy < 0.68, accuracy


def test_holding_out_keeps_the_two_slices_disjoint_and_whole():
    line = ordering(*([n] for n in range(40)))
    extracted = trainer.extract(line, seed=3, explicit=answered(line))

    held_out, training = trainer.hold_out(extracted, share=0.25, seed=3)

    assert held_out and training
    assert len(held_out) + len(training) == len(extracted)
    assert not {id(pair) for pair in held_out} & {id(pair) for pair in training}


def test_accuracy_is_unanswerable_where_nothing_directional_was_held_back():
    """A tie has no direction to get right, so a held-out slice of ties measures nothing."""
    rows = {film.tmdb_id: film for film in library(4)}
    ties = [trainer.Pair(a=9000, b=9001, target=trainer.TIED, weight=1.0, explicit=True)]
    space = features.learn(list(rows.values()))

    weights = trainer.fit(trainer.design(ties, space, rows))
    assert trainer.accuracy(weights, trainer.design(ties, space, rows)) is None


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
    line = ordering(*([film.tmdb_id] for film in ranked))
    extracted = trainer.extract(line, seed=3, explicit=answered(line))
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


def test_a_seed_shaped_library_retrains_without_a_pair_by_feature_matrix():
    """What keeps the retrain inside the worker's memory, pinned as an allocation bound.

    A seed import parks hundreds of films in ten tie-groups, and every within-group and
    adjacent-group pair is a training row - some eighty thousand of them for one real
    library, against a feature space in the low thousands. A row-per-pair matrix of that
    is over a gigabyte, and on the live box it killed the worker (#59).

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
