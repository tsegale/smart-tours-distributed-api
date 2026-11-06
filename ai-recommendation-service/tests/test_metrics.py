"""The evaluation's maths, checked by hand: a wrong metric would decide everything wrongly and look fine."""

import math

import pytest

from app.retrieval import rrf_score
from eval.metrics import bootstrap_ci, ndcg_at, paired_difference_ci, percentile, recall_at, reciprocal_rank


def test_rrf_by_hand():
    assert rrf_score([1, 1], k=60) == pytest.approx(2 / 61)
    assert rrf_score([1, None], k=60) == pytest.approx(1 / 61)
    assert rrf_score([None, None], k=60) == 0.0


def test_rrf_favours_appearing_in_both_lists():
    assert rrf_score([10, 10], k=60) > rrf_score([1, None], k=60)


def test_a_small_k_rewards_the_very_top():
    top_vs_tenth = lambda k: rrf_score([1], k=k) / rrf_score([10], k=k)  # noqa: E731
    assert top_vs_tenth(1) > top_vs_tenth(60)


def test_a_perfect_ranking_scores_one():
    assert ndcg_at([1, 2, 3], {1: 3, 2: 2, 3: 1}) == pytest.approx(1.0)


def test_ndcg_by_hand():
    # Grades 0 then 3, against an ideal of 3 alone: (2^3 - 1) / log2(3) over (2^3 - 1) / log2(2).
    assert ndcg_at([9, 1], {1: 3}) == pytest.approx(1 / math.log2(3))


def test_ndcg_only_counts_the_top_k():
    assert ndcg_at([9, 9, 1], {1: 3}, k=2) == 0.0


def test_ndcg_with_nothing_relevant_is_zero():
    assert ndcg_at([1, 2], {}) == 0.0


def test_a_graded_order_matters():
    assert ndcg_at([1, 2], {1: 3, 2: 1}) > ndcg_at([2, 1], {1: 3, 2: 1})


def test_recall():
    assert recall_at([1, 9], {1: 3, 2: 1, 3: 0}) == pytest.approx(0.5)
    assert recall_at([1, 2], {}) == 0.0


def test_recall_only_counts_the_top_k():
    assert recall_at([9, 1], {1: 2}, k=1) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank([9, 1], {1: 3}) == pytest.approx(0.5)
    assert reciprocal_rank([9], {1: 3}) == 0.0


def test_reciprocal_rank_ignores_a_weak_match():
    assert reciprocal_rank([1, 2], {1: 1, 2: 3}) == pytest.approx(0.5)


def test_percentile():
    assert percentile(list(range(1, 101)), 0.5) == 51
    assert percentile([5.0], 0.95) == 5.0


def test_a_constant_has_no_interval():
    assert bootstrap_ci([0.5] * 20) == (pytest.approx(0.5), pytest.approx(0.5), pytest.approx(0.5))


def test_the_interval_contains_the_mean_and_is_repeatable():
    values = [0.0, 1.0] * 25
    mean, low, high = bootstrap_ci(values)
    assert low < mean < high
    assert bootstrap_ci(values) == (mean, low, high)


def test_more_queries_narrow_the_interval():
    few = bootstrap_ci([0.0, 1.0] * 10)
    many = bootstrap_ci([0.0, 1.0] * 200)
    assert many[2] - many[1] < few[2] - few[1]


def test_a_paired_difference_sees_what_separate_intervals_hide():
    # Noisy queries, but b beats a by the same amount on every one.
    a = [i / 100 for i in range(50)]
    b = [v + 0.02 for v in a]
    difference, low, high = paired_difference_ci(b, a)
    assert difference == pytest.approx(0.02)
    assert low > 0
    assert high == pytest.approx(0.02)
