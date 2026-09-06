"""The weight vector: pairs read out of the ordering, and the logistic fit over them.

Feature-parameterised Bradley-Terry (ADR 0004): the model reads the *difference* between
two films' feature vectors and answers how likely the owner is to prefer the first. That
shape is what lets one fit serve both scoring jobs - a single film's score is its vector
against the zero vector - and what lets it score a film nobody has rated, since a film
it has never seen is still made of features it has.

The fit is deliberately linear and deliberately from scratch. Retraining the whole thing
on every ordering change costs milliseconds at library scale, which is cheaper than any
scheme for keeping an incremental model honest, and it means the vector can never be
subtly out of date with the judgments it claims to summarise.

Four decisions about the pairs, all of them the spec's (ADR 0013):

*Across bands, order is a judgment.* Every film draws opponents from the bands above and
below it, adjacent bands and long-range alike, up to a budget per film. These carry full
weight, and the band gap is what teaches magnitude - that the distance from a 5.0 to a
1.0 is not the distance from a 5.0 to a 4.5. The budget is what keeps a band of a
hundred films costing hundreds of pairs rather than thousands (#59).

*Within a band, order is a range.* Pairs inside a band are sampled per film and weighted
by the distance between the two films as a fraction of the band's span, so neighbours
train as near-equals and the top of a band against its bottom trains as a judgment. One
rank apart is, to the engine, very nearly the same film. This is what lets a strict order
stand in for the judgments the owner cannot actually make between neighbours.

*Explicit band comparisons outweigh implied pairs - where they still agree.* A pair the
owner actually answered is worth more than one the ordering merely implies, but the
ordering is primary state and the log is evidence about it (ADR 0010): an answer the
owner has since moved past is dropped rather than argued with, because a later move wins.

*No recency decay, and no ties.* The ordering as it stands is the signal, and the owner's
moves are the one mechanism that owns taste change. Nothing is provisional and nothing is
tied, so there is no discount to apply and no equality target to hit.

There is no intercept, by construction: the model reads a difference, so it must answer
``1 - p`` when handed the pair the other way round. A bias term would mean "A wins by
default", which says nothing about anybody's taste.
"""

import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from anchor.features import FeatureSpace
from anchor.models import Film
from anchor.ordering import Ordering, Placed

L2 = 1e-3
"""How hard the fit is pulled back towards zero. Small: the pairs are few and the
features many, so some pull is what keeps one lucky keyword from carrying the vector."""

ITERATIONS = 400
"""Accelerated gradient steps. Fixed rather than convergence-checked, so a retrain costs
the same every time and the job's runtime is a constant the operator can reason about."""

POWER_ITERATIONS = 20
"""Steps of the power iteration that measures the data's curvature, to size the step."""


@dataclass(frozen=True)
class Sampling:
    """The extraction knobs taste-profile.md leaves to implementation tuning.

    Validated empirically rather than derived: what matters to the spec is the shape -
    cross-band at full weight, within-band by distance, explicit above implied - and
    these are the numbers that hold that shape at library scale.
    """

    adjacent_per_film: int = 4
    """Opponents a film draws from each of the two nearest occupied bands, either side.

    Drawn from both sides so every film in a big imported band stands above some of the
    band below and below some of the band above, whatever the bands' sizes.
    """

    long_range_per_film: int = 4
    """Opponents drawn from the bands further off than those. What teaches magnitude."""

    within_per_film: int = 4
    """Opponents a film draws from its own band, where the band holds more than this."""

    explicit_weight: float = 3.0
    """What an answer the owner gave is worth against a pair the ordering merely implies."""


SAMPLING = Sampling()
"""The defaults, as one shared value: the knobs are constants, not per-call arguments."""


@dataclass(frozen=True)
class Answered:
    """One band comparison the owner gave, as they gave it: the winner and the loser.

    Read off the log and checked against the ordering rather than trusted outright, so
    the direction here is the owner's answer and not yet a claim about anything.
    """

    better: int
    worse: int


@dataclass(frozen=True)
class Pair:
    """One training example: two rated films, which way the ordering has them, and how
    much that counts."""

    a: int
    b: int
    """``a`` is the film the ordering puts above ``b``."""
    weight: float
    explicit: bool
    """The owner answered this exact pair, rather than the ordering implying it."""
    within_band: bool
    """Both films sit in one band, so this pair is a range rather than a verdict."""

    @property
    def target(self) -> float:
        """Always a win for ``a``: there are no ties in the ordering to aim at."""
        return 1.0


def extract(
    ordering: Ordering,
    *,
    seed: int,
    explicit: Collection[Answered] = (),
    sampling: Sampling = SAMPLING,
) -> list[Pair]:
    """Read the ordering into the pairs that train on it.

    ``explicit`` is the band comparisons the owner actually answered. They are kept where
    the ordering still agrees with them and dropped where it does not - the ordering is
    primary and a later move wins (ADR 0013) - and where kept they carry the explicit
    weight rather than a new direction, since the direction is the ordering's to state.

    Sampling takes ``seed`` so a scripted flow retrains identically every run
    (testing.md).
    """
    rng = random.Random(seed)
    pairs: list[Pair] = []
    seen: set[frozenset[int]] = set()
    agreeing = _agreeing(explicit, ordering)
    answered = {frozenset({one.better, one.worse}) for one in agreeing}

    def emit(a: int, b: int, *, weight: float, within_band: bool) -> None:
        if a == b or (key := frozenset({a, b})) in seen or weight <= 0.0:
            return
        seen.add(key)
        pairs.append(
            Pair(
                a=a,
                b=b,
                weight=sampling.explicit_weight if key in answered else weight,
                explicit=key in answered,
                within_band=within_band,
            )
        )

    # The owner's own answers first, and never left to the sampler's luck: a budget of
    # four opponents per film would drop most of them at library scale, and they are the
    # highest-value evidence there is. Their *direction* still comes off the ordering,
    # because the ordering is primary state and the log is evidence about it (ADR 0010).
    for one in agreeing:
        above, below = ordering.of(one.better), ordering.of(one.worse)
        assert above is not None and below is not None  # _agreeing dropped the rest
        emit(
            one.better,
            one.worse,
            weight=1.0,
            within_band=above.band == below.band,
        )

    bands = ordering.bands()
    for position, band in enumerate(bands):
        row = ordering.row(band)
        for placed in row:
            for other in _within_band_opponents(rng, row, placed, sampling.within_per_film):
                above, below = (placed, other) if placed.rank < other.rank else (other, placed)
                emit(
                    above.film_id,
                    below.film_id,
                    weight=_within_band_weight(above.rank, below.rank, len(row)),
                    within_band=True,
                )

        near = [bands[other] for other in (position - 1, position + 1) if 0 <= other < len(bands)]
        far = [
            other
            for index, other in enumerate(bands)
            if abs(index - position) > 1 and index != position
        ]
        for placed in row:
            for opponent_band in near:
                for opponent in _some(rng, ordering.row(opponent_band), sampling.adjacent_per_film):
                    _emit_cross_band(emit, placed, opponent, band, opponent_band)
            for opponent_band in _some_bands(rng, far, sampling.long_range_per_film):
                opponent = rng.choice(ordering.row(opponent_band))
                _emit_cross_band(emit, placed, opponent, band, opponent_band)
    return pairs


def _emit_cross_band(emit, subject, opponent, band: float, opponent_band: float) -> None:  # type: ignore[no-untyped-def]
    """One cross-band pair at full weight, the better band's film first."""
    if band > opponent_band:
        emit(subject.film_id, opponent.film_id, weight=1.0, within_band=False)
    else:
        emit(opponent.film_id, subject.film_id, weight=1.0, within_band=False)


def _within_band_weight(better_rank: int, worse_rank: int, size: int) -> float:
    """How much a within-band pair counts: the gap between the two, over the band's span.

    A band of ``size`` films spans ``size - 1`` ranks, so neighbours in a long row come
    out near zero and the row's top against its bottom comes out at one - a full judgment,
    which is what the spec asks for. A two-film band is the degenerate case of exactly
    that: its one pair *is* its top against its bottom.
    """
    span = max(size - 1, 1)
    return (worse_rank - better_rank) / span


def _agreeing(explicit: Collection[Answered], ordering: Ordering) -> list[Answered]:
    """The owner's band comparisons the ordering still stands behind.

    Contradiction is judged at the band, because that is what a band comparison was
    about: an answer whose winner now sits in a *worse* band has been overruled by a
    later act of the owner's and is dropped. An answer whose two films ended up in one
    band is not overruled - within-band order is a range, not a verdict - so it stands,
    and it stands as the real answer it is rather than as a range.

    Sorted rather than taken in set order, so a retrain over unchanged data produces the
    same pairs in the same order every time: reproducibility the metrics log depends on.
    """
    kept = []
    for one in sorted(explicit, key=lambda answer: (answer.better, answer.worse)):
        better, worse = ordering.of(one.better), ordering.of(one.worse)
        if better is None or worse is None:
            continue
        if better.band < worse.band:
            continue
        kept.append(one)
    return kept


def _some(rng: random.Random, pool: Sequence[Any], budget: int) -> Sequence[Any]:
    """Up to ``budget`` of the pool - the whole pool where it fits, drawn where it does not.

    The rng is only consulted where a draw happens, so an ordering with no band over
    budget extracts exactly as it did before there was one, long-range sample included.
    """
    return pool if len(pool) <= budget else rng.sample(pool, budget)


def _some_bands(rng: random.Random, bands: Sequence[float], budget: int) -> Sequence[float]:
    return bands if len(bands) <= budget else rng.sample(bands, budget)


def _within_band_opponents(
    rng: random.Random, row: Sequence[Placed], placed: Placed, budget: int
) -> Sequence[Placed]:
    """Up to ``budget`` other films of the same band.

    One more than the budget is drawn and the film itself dropped if it came up, which
    is cheaper than building the row-minus-self once per member of a long row.
    """
    if len(row) - 1 <= budget:
        return [other for other in row if other.film_id != placed.film_id]
    drawn = rng.sample(row, budget + 1)
    return [other for other in drawn if other.film_id != placed.film_id][:budget]


def hold_out(pairs: Sequence[Pair], *, share: float, seed: int) -> tuple[list[Pair], list[Pair]]:
    """Split off a slice to measure against, and hand back the rest to train on.

    The slice is the cross-band pairs together with the owner's own band comparisons
    (evaluation.md). Within-band pairs are excluded from it, because the ordering itself
    calls them a range rather than a verdict: scoring the model on whether it can tell
    two neighbours apart would be marking it against a question the ordering does not
    claim to answer. Where nothing eligible exists, nothing is held out and the retrain
    reports no accuracy, which is honest rather than flattering.
    """
    eligible = [index for index, pair in enumerate(pairs) if pair.explicit or not pair.within_band]
    wanted = int(len(eligible) * share)
    if wanted == 0:
        return [], list(pairs)
    held_out = set(random.Random(seed).sample(eligible, wanted))
    return (
        [pairs[index] for index in sorted(held_out)],
        [pair for index, pair in enumerate(pairs) if index not in held_out],
    )


@dataclass(frozen=True)
class Design:
    """Pairs as the fit reads them: one row of feature differences per pair.

    The rows are never built. Each is ``features(a) - features(b)``, so every product
    the fit needs factors through the film matrix: ``X @ w`` is ``(films @ w)`` read out
    at ``a`` minus at ``b``, and ``X.T @ r`` is ``films.T`` applied to ``r`` summed onto
    ``a`` minus summed onto ``b``. What is held is the films-by-features matrix and two
    index arrays, so memory follows the library and never the pair count - a seed import
    parks hundreds of films in ten tie-groups, and the pairs that implies, times the
    feature space, is the matrix that killed the worker (#59).
    """

    films: np.ndarray
    """One row per distinct film in the pairs, in this space."""
    a: np.ndarray
    b: np.ndarray
    """Row indices into ``films``, one pair per position."""
    y: np.ndarray
    weight: np.ndarray

    def __len__(self) -> int:
        return len(self.y)

    @property
    def width(self) -> int:
        return int(self.films.shape[1])

    def apply(self, vector: np.ndarray) -> np.ndarray:
        """``X @ vector``: each pair's difference, read through the film scores."""
        scored: np.ndarray = self.films @ vector
        differences: np.ndarray = scored[self.a] - scored[self.b]
        return differences

    def apply_transposed(self, residual: np.ndarray) -> np.ndarray:
        """``X.T @ residual``: each film's share of the residual, read through the features."""
        rows = len(self.films)
        per_film = np.bincount(self.a, residual, rows) - np.bincount(self.b, residual, rows)
        shares: np.ndarray = self.films.T @ per_film
        return shares


def design(pairs: Sequence[Pair], space: FeatureSpace, films: Mapping[int, Film]) -> Design:
    """Vectorise the pairs: each row is ``features(a) - features(b)``, held factored."""
    usable = [pair for pair in pairs if pair.a in films and pair.b in films]
    # Vectorised once per film rather than once per appearance: at library scale each
    # film sits in a dozen pairs, and this is the difference between a retrain that
    # costs milliseconds and one that costs most of a second.
    # The distinct films first: keying the comprehension on the pairs would evaluate the
    # vector once per appearance and overwrite, which is the cost this exists to avoid.
    appearing = sorted({film_id for pair in usable for film_id in (pair.a, pair.b)})
    row = {film_id: index for index, film_id in enumerate(appearing)}
    matrix = np.empty((len(appearing), len(space)))
    for film_id, index in row.items():
        matrix[index] = space.vector(films[film_id])
    return Design(
        films=matrix,
        a=np.array([row[pair.a] for pair in usable], dtype=np.intp),
        b=np.array([row[pair.b] for pair in usable], dtype=np.intp),
        y=np.array([pair.target for pair in usable]),
        weight=np.array([pair.weight for pair in usable]),
    )


def fit(design: Design, *, l2: float = L2, iterations: int = ITERATIONS) -> np.ndarray:
    """L2-regularised logistic regression on the pair differences, weights per pair.

    Nesterov-accelerated gradient descent with the step read off the data's own
    curvature, so there is no learning rate to tune and no way for one library's feature
    scale to make the fit diverge where another's converged.
    """
    if len(design) == 0 or design.width == 0:
        return np.zeros(design.width)
    total = float(design.weight.sum()) or 1.0
    step = 1.0 / (0.25 * _curvature(design) / total + l2)

    weights = np.zeros(design.width)
    ahead = weights
    for iteration in range(iterations):
        residual = design.weight * (_logistic(design.apply(ahead)) - design.y)
        gradient = design.apply_transposed(residual) / total + l2 * ahead
        stepped = ahead - step * gradient
        ahead = stepped + (iteration / (iteration + 3)) * (stepped - weights)
        weights = stepped
    return weights


def accuracy(weights: np.ndarray, design: Design) -> float | None:
    """The share of the held-out pairs the vector gets the right way round.

    None where nothing was measured against, which is honest rather than flattering:
    a fresh account has no cross-band pairs and no answers, so it has no accuracy.
    """
    if len(design) == 0:
        return None
    return float((design.apply(weights) > 0).mean())


def score(weights: np.ndarray, space: FeatureSpace, film: Film) -> float:
    """One film's standing under this vector. Internal: ADR 0005 keeps it off every surface."""
    return float(space.vector(film) @ weights)


def _logistic(z: np.ndarray) -> np.ndarray:
    """The logistic function via tanh, which is the overflow-free way to write it."""
    return 0.5 * (1.0 + np.tanh(0.5 * z))


def _curvature(design: Design) -> float:
    """An upper bound on how sharply the loss bends, found by power iteration.

    The largest eigenvalue of the weighted design, which is what sets the largest step
    the fit can take without overshooting.

    Two starts, because one is not safe. The all-ones vector reads every film as the sum
    of its feature values, so a library whose films all carry the same *number* of
    symbols - every difference falling on one column - hands the iteration a vector the
    design annihilates, and it would report a curvature of zero for data that has plenty.
    A step sized off that zero is the whole of 1/l2, which is a thousand times too far
    and leaves the fit at nothing at all. The second start is deterministic, so a retrain
    is still reproducible; only a design that really is empty reaches zero.
    """
    for start in (np.ones(design.width), np.random.default_rng(0).normal(size=design.width)):
        if (found := _power_iterate(design, start)) > 0.0:
            return found
    return 0.0


def _power_iterate(design: Design, vector: np.ndarray) -> float:
    for _ in range(POWER_ITERATIONS):
        vector = design.apply_transposed(design.weight * design.apply(vector))
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return 0.0
        vector /= norm
    return float(np.linalg.norm(design.apply_transposed(design.weight * design.apply(vector))))
