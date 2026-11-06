"""The held-out set stays out of everything that measures until the owner has reviewed it."""

from pathlib import Path

from eval.curated import CURATED
from eval.curated_coverage import CURATED_COVERAGE
from eval.heldout import HELDOUT, NEEDS_REVIEW
from eval.run_eval import curated_queries, load_tours, query_sets

EVAL = Path(__file__).parents[1] / "eval"


def test_it_is_24_new_queries_in_english_and_german_pairs():
    assert len(HELDOUT) == 24
    seen = {query for _, _, query, _ in CURATED + CURATED_COVERAGE}
    assert not seen & {query for _, _, query, _ in HELDOUT}
    for english, german in zip(HELDOUT[::2], HELDOUT[1::2], strict=True):
        assert (english[0], german[0]) == ("en", "de")
        assert english[3] is german[3]


def test_it_labels_real_tours():
    curated_queries(load_tours(), HELDOUT, "heldout")  # raises on a name that isn't a tour


def test_until_it_is_reviewed_nothing_measures_it():
    if not NEEDS_REVIEW:
        return
    assert "heldout" not in query_sets(load_tours())
    importers = [p.name for p in EVAL.glob("*.py") if p.name != "heldout.py" and "heldout" in p.read_text("utf-8")]
    assert importers == []
