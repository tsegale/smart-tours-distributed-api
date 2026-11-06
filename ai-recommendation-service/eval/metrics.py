"""Ranking metrics and bootstrap confidence intervals. Plain Python, so that they can be tested by hand."""

import math
import random
from collections.abc import Callable, Mapping, Sequence


def dcg(grades: Sequence[int]) -> float:
    return sum((2**grade - 1) / math.log2(position + 2) for position, grade in enumerate(grades))


def ndcg_at(ranking: Sequence[int], judgments: Mapping[int, int], k: int = 10) -> float:
    ideal = dcg(sorted(judgments.values(), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return dcg([judgments.get(item, 0) for item in ranking[:k]]) / ideal


def recall_at(ranking: Sequence[int], judgments: Mapping[int, int], k: int = 20) -> float:
    relevant = {item for item, grade in judgments.items() if grade > 0}
    if not relevant:
        return 0.0
    return len(relevant & set(ranking[:k])) / len(relevant)


def reciprocal_rank(ranking: Sequence[int], judgments: Mapping[int, int], min_grade: int = 2) -> float:
    for position, item in enumerate(ranking):
        if judgments.get(item, 0) >= min_grade:
            return 1.0 / (position + 1)
    return 0.0


def percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def bootstrap_ci(
    per_query: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = lambda v: sum(v) / len(v),
    rounds: int = 2000,
    seed: int = 7,
) -> tuple[float, float, float]:
    """(point estimate, low, high): a 95% interval from resampling queries, so small differences aren't over-read."""
    rng = random.Random(seed)
    size = len(per_query)
    samples = sorted(statistic([per_query[rng.randrange(size)] for _ in range(size)]) for _ in range(rounds))
    return statistic(per_query), samples[int(0.025 * rounds)], samples[int(0.975 * rounds)]


def paired_difference_ci(
    a: Sequence[float], b: Sequence[float], rounds: int = 2000, seed: int = 7
) -> tuple[float, float, float]:
    """The difference a - b, resampling queries together: the right test for two systems run on one query set."""
    return bootstrap_ci([x - y for x, y in zip(a, b, strict=True)], rounds=rounds, seed=seed)
