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

Five decisions about the pairs, all of them the spec's:

*Every pair the owner actually answered.* They are the highest-value signal there is, so
they are never left to the sampler's luck - but their *direction* is read off the
ordering, never off the answer, because the ordering is the primary state and the log is
evidence about it (ADR 0010).

*Every adjacent pair, always.* Adjacency fully captures the order, so it is never
sampled; long-range pairs are, and they are what teach magnitude - that the gap between
the owner's first and fiftieth film is not the gap between their first and second.

*Ties are equality targets, not missing data.* "These two are the same to me" is a
judgment, and the model is told so.

*Explicit answers outweigh implied pairs, and provisional placements are discounted.*
Both are the same idea: a pair the owner actually answered is worth more than one the
ordering merely implies, and a position the owner has not really settled is worth less.

*No recency decay.* The ordering as it stands is the signal; drift resolution is the one
mechanism that owns taste change, and a second silent correction channel would blur it.

There is no intercept, by construction: the model reads a difference, so it must answer
``1 - p`` when handed the pair the other way round. A bias term would mean "A wins by
default", which says nothing about anybody's taste.
"""

import random
from collections.abc import Collection, Container, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from anchor.features import FeatureSpace
from anchor.models import Film
from anchor.ordering import Ordering

TIED = 0.5
"""The target for a pair the owner judged equal: neither film is likelier to win."""

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
    explicit above implied, provisional below both - and these are the numbers that hold
    that shape at library scale.
    """

    long_range_per_film: int = 4
    """Distant opponents sampled per film. Enough to teach magnitude, few enough that
    adjacency - the part that actually carries the order - is not drowned out."""

    explicit_weight: float = 3.0
    """What an answer the owner gave is worth against a pair the ordering merely implies."""

    provisional_weight: float = 0.4
    """What a pair touching an unsettled position is worth. It still says something."""


SAMPLING = Sampling()
"""The defaults, as one shared value: the knobs are constants, not per-call arguments."""


@dataclass(frozen=True)
class Pair:
    """One training example: two rated films, which way the ordering has them, and how much
    that counts."""

    a: int
    b: int
    target: float
    """1.0 where ``a`` sits above ``b``; :data:`TIED` where the owner judged them equal."""
    weight: float
    explicit: bool
    """The owner answered this exact pair, rather than the ordering implying it."""


def extract(
    ordering: Ordering,
    *,
    seed: int,
    explicit: Collection[frozenset[int]] = frozenset(),
    provisional: Container[int] = frozenset(),
    sampling: Sampling = SAMPLING,
) -> list[Pair]:
    """Read the ordering into the pairs that train on it.

    ``explicit`` is the unordered pairs the owner actually answered and ``provisional``
    the films whose placement is not yet fully trusted; both only ever change a pair's
    weight, never which pairs exist or which way they point. Sampling takes ``seed`` so a
    scripted flow retrains identically every run (testing.md).
    """
    rng = random.Random(seed)
    pairs: list[Pair] = []
    seen: set[frozenset[int]] = set()

    def emit(a: int, b: int, target: float) -> None:
        if a == b or (key := frozenset({a, b})) in seen:
            return
        seen.add(key)
        weight = sampling.explicit_weight if key in explicit else 1.0
        if a in provisional or b in provisional:
            weight *= sampling.provisional_weight
        pairs.append(Pair(a=a, b=b, target=target, weight=weight, explicit=key in explicit))

    slots = ordering.slots
    seats = {film_id: index for index, slot in enumerate(slots) for film_id in slot.film_ids}
    for answered in _answered(explicit, seats):
        above, below = answered
        emit(above, below, TIED if seats[above] == seats[below] else 1.0)

    for index, slot in enumerate(slots):
        for position, film_id in enumerate(slot.film_ids):
            for other in slot.film_ids[position + 1 :]:
                emit(film_id, other, TIED)
        if index + 1 < len(slots):
            for film_id in slot.film_ids:
                for below in slots[index + 1].film_ids:
                    emit(film_id, below, 1.0)

    for index, slot in enumerate(slots):
        distant = [other for other in range(len(slots)) if abs(other - index) > 1]
        for film_id in slot.film_ids:
            for far in rng.sample(distant, min(sampling.long_range_per_film, len(distant))):
                opponent = rng.choice(slots[far].film_ids)
                emit(*((film_id, opponent) if far > index else (opponent, film_id)), 1.0)
    return pairs


def _answered(
    explicit: Collection[frozenset[int]], seats: Mapping[int, int]
) -> list[tuple[int, int]]:
    """The answered pairs both of whose films are still rated, better film first.

    Sorted rather than taken in set order, so a retrain over unchanged data produces the
    same pairs in the same order every time - reproducibility the metrics log depends on.
    """
    ordered = []
    for pair in sorted(sorted(one) for one in explicit):
        a, b = pair
        if a in seats and b in seats:
            ordered.append((a, b) if seats[a] <= seats[b] else (b, a))
    return ordered


def hold_out(pairs: Sequence[Pair], *, share: float, seed: int) -> tuple[list[Pair], list[Pair]]:
    """Split off a slice to measure against, and hand back the rest to train on.

    The slice is drawn from the pairs the owner *answered*, because those are the only
    ones with an independent right answer in them - an implied pair is a restatement of
    the ordering the model is being fitted to, and predicting it proves nothing. Where
    the account has no explicit answers yet, nothing is held out and the retrain simply
    reports no accuracy, which is honest rather than flattering.
    """
    answered = [index for index, pair in enumerate(pairs) if pair.explicit and pair.target != TIED]
    wanted = int(len(answered) * share)
    if wanted == 0:
        return [], list(pairs)
    held_out = set(random.Random(seed).sample(answered, wanted))
    return (
        [pairs[index] for index in sorted(held_out)],
        [pair for index, pair in enumerate(pairs) if index not in held_out],
    )


@dataclass(frozen=True)
class Design:
    """Pairs as the fit reads them: one row of feature differences per pair."""

    x: np.ndarray
    y: np.ndarray
    weight: np.ndarray

    def __len__(self) -> int:
        return len(self.y)


def design(pairs: Sequence[Pair], space: FeatureSpace, films: Mapping[int, Film]) -> Design:
    """Vectorise the pairs: each row is ``features(a) - features(b)``."""
    usable = [pair for pair in pairs if pair.a in films and pair.b in films]
    # Vectorised once per film rather than once per appearance: at library scale each
    # film sits in a dozen pairs, and this is the difference between a retrain that
    # costs milliseconds and one that costs most of a second.
    vectors = {
        film_id: space.vector(films[film_id]) for pair in usable for film_id in (pair.a, pair.b)
    }
    rows = [vectors[pair.a] - vectors[pair.b] for pair in usable]
    return Design(
        x=np.array(rows).reshape(len(usable), len(space)),
        y=np.array([pair.target for pair in usable]),
        weight=np.array([pair.weight for pair in usable]),
    )


def fit(design: Design, *, l2: float = L2, iterations: int = ITERATIONS) -> np.ndarray:
    """L2-regularised logistic regression on the pair differences, weights per pair.

    Nesterov-accelerated gradient descent with the step read off the data's own
    curvature, so there is no learning rate to tune and no way for one library's feature
    scale to make the fit diverge where another's converged.
    """
    if len(design) == 0 or design.x.shape[1] == 0:
        return np.zeros(design.x.shape[1])
    total = float(design.weight.sum()) or 1.0
    step = 1.0 / (0.25 * _curvature(design) / total + l2)

    weights = np.zeros(design.x.shape[1])
    ahead = weights
    for iteration in range(iterations):
        residual = design.weight * (_logistic(design.x @ ahead) - design.y)
        gradient = design.x.T @ residual / total + l2 * ahead
        stepped = ahead - step * gradient
        ahead = stepped + (iteration / (iteration + 3)) * (stepped - weights)
        weights = stepped
    return weights


def accuracy(weights: np.ndarray, design: Design) -> float | None:
    """The share of directional pairs the vector gets the right way round.

    None where nothing directional was measured against: a tie has no direction to get
    right, so a slice holding only ties says nothing about the scorer either way.
    """
    directional = design.y != TIED
    if not directional.any():
        return None
    predicted = design.x[directional] @ weights > 0
    return float((predicted == (design.y[directional] > TIED)).mean())


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
    """
    vector = np.ones(design.x.shape[1])
    for _ in range(POWER_ITERATIONS):
        vector = design.x.T @ (design.weight * (design.x @ vector))
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return 0.0
        vector /= norm
    return float(np.linalg.norm(design.x.T @ (design.weight * (design.x @ vector))))
