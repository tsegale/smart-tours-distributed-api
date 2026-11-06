"""The two signed-off query sets: labelled apart, reported side by side, each with its own lines in the gate."""

from eval.coverage_analysis import strip_negation
from eval.curated import CURATED, NEEDS_REVIEW, REVIEWED_ON
from eval.curated_coverage import CURATED_COVERAGE
from eval.curated_coverage import NEEDS_REVIEW as COVERAGE_NEEDS_REVIEW
from eval.curated_coverage import REVIEWED_ON as COVERAGE_REVIEWED_ON
from eval.run_eval import curated_queries, load_tours, query_sets

STILL_UNLABELLED = {"Tigerfish on the Zambezi", "Photo boat with swivel seats", "Stone church and a railway past"}


def labelled(source: list[tuple]) -> set[str]:
    return {name for *_, judgments in source for name in judgments}


def test_both_sets_are_signed_off():
    assert NEEDS_REVIEW is False
    assert REVIEWED_ON == "2026-11-06"
    assert len(CURATED) == 50
    assert COVERAGE_NEEDS_REVIEW is False
    assert COVERAGE_REVIEWED_ON == "2026-11-06"


def test_no_query_is_in_both_sets():
    assert not {query for _, _, query, _ in CURATED} & {query for _, _, query, _ in CURATED_COVERAGE}


def test_the_coverage_set_comes_in_english_and_german_pairs_with_matching_labels():
    assert len(CURATED_COVERAGE) % 2 == 0
    for english, german in zip(CURATED_COVERAGE[::2], CURATED_COVERAGE[1::2], strict=True):
        assert (english[0], german[0]) == ("en", "de")
        assert english[3] == german[3]


def test_the_coverage_set_labels_real_tours_and_reaches_what_it_says():
    tours = load_tours()
    curated_queries(tours, CURATED_COVERAGE, "coverage")  # raises on a name that isn't a tour
    written = {tour["name"] for tour in tours if tour["authored"]}
    gap = written - labelled(CURATED)
    assert len(gap) == 23
    assert gap - labelled(CURATED_COVERAGE) == STILL_UNLABELLED


def test_coverage_is_a_set_of_its_own_and_never_averaged_into_curated():
    tours = load_tours()
    sets = query_sets(tours)
    assert len(sets["curated"]) == 50
    assert len(sets["coverage"]) == len(CURATED_COVERAGE)
    assert not {q["id"] for q in sets["curated"]} & {q["id"] for q in sets["coverage"]}
    assert [q["q"] for q in curated_queries(tours)] == [query for _, _, query, _ in CURATED]


def test_stripping_a_negation_keeps_what_is_wanted():
    assert strip_negation("exploring the wild, not sitting in a safari car") == "exploring the wild"
    assert strip_negation("die Wildnis erkunden, statt im Safariwagen zu sitzen") == "die Wildnis erkunden"
    assert strip_negation("rocks without anything demanding") == "rocks"
    assert strip_negation("dunes at sunrise") == "dunes at sunrise"
    assert strip_negation("not a query with anything left") == "not a query with anything left"
