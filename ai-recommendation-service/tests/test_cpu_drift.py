"""Issue #10: evaluations record the CPU, and the gate's tolerance comes from what the survey measured."""

import json
from pathlib import Path

from eval.cpu import cpu_features
from eval.regression import TOLERANCE, regressions

SURVEY = Path(__file__).parents[1] / "eval" / "results" / "cpu_survey.json"
GATE_SURVEY = Path(__file__).parents[1] / "eval" / "results" / "cpu_survey_gate.json"
BASELINE = Path(__file__).parents[1] / "eval" / "baseline.json"


def cpuinfo(tmp_path, model, flags):
    path = tmp_path / "cpuinfo"
    block = f"processor\t: 0\nmodel name\t: {model}\nflags\t\t: {flags}\n\n"
    path.write_text(block * 2, encoding="utf-8")
    return path


def test_an_intel_cpu_with_avx512_vnni(tmp_path):
    path = cpuinfo(tmp_path, "Intel Xeon", "fpu avx2 avx512f avx512_vnni")
    assert cpu_features(path) == {"model": "Intel Xeon", "vnni": True, "avx512": True, "avx2": True}


def test_avx_vnni_counts_as_vnni_without_avx512(tmp_path):
    path = cpuinfo(tmp_path, "Intel Core", "fpu avx2 avx_vnni")
    assert cpu_features(path) == {"model": "Intel Core", "vnni": True, "avx512": False, "avx2": True}


def test_a_cpu_with_avx2_only(tmp_path):
    path = cpuinfo(tmp_path, "AMD EPYC 7763", "fpu avx2")
    assert cpu_features(path) == {"model": "AMD EPYC 7763", "vnni": False, "avx512": False, "avx2": True}


def test_no_cpuinfo_says_unknown_rather_than_no(tmp_path):
    assert cpu_features(tmp_path / "missing") == {"model": None, "vnni": None, "avx512": None, "avx2": None}


def spread(row: dict, metric: str) -> float:
    values = row["vnni"][metric] + row["no_vnni"][metric]
    return max(values) - min(values)


def test_the_tolerance_covers_the_shipped_models_spread_and_not_the_old_ones():
    variants = json.loads(SURVEY.read_text(encoding="utf-8"))["variants"]
    model = json.loads(BASELINE.read_text(encoding="utf-8"))["model"]
    assert model == "paraphrase-ml-uint8"
    shipped = max(spread(variants[model], metric) for metric in ("gate_ndcg", "gate_recall"))
    old = max(spread(variants["paraphrase-ml-int8"], metric) for metric in ("gate_ndcg", "gate_recall"))
    assert shipped <= TOLERANCE < old


def test_the_tolerance_covers_every_gate_line_including_coverage():
    survey = json.loads(GATE_SURVEY.read_text(encoding="utf-8"))
    assert survey["jobs"]["vnni"] and survey["jobs"]["no_vnni"]
    lines = survey["gate"]["paraphrase-ml-uint8"]
    assert {"coverage hybrid ndcg@10", "coverage hybrid recall@20"} <= set(lines)
    assert max(line["spread"] for line in lines.values()) <= TOLERANCE
    assert survey["gate"]["paraphrase-ml-int8"]["coverage hybrid ndcg@10"]["spread"] > TOLERANCE


def lines(ndcg: float) -> dict:
    return {"lexical": {"ndcg@10": 0.2}, "hybrid": {"ndcg@10": ndcg}}


def test_a_fall_on_coverage_fails_the_gate_even_when_curated_holds():
    baseline = {**lines(0.77), "coverage": lines(0.45)}
    assert regressions(baseline, baseline) == []
    assert regressions(baseline, {**lines(0.77), "coverage": lines(0.45 - TOLERANCE + 0.0001)}) == []
    assert regressions(baseline, {**lines(0.77), "coverage": lines(0.43)}) == [
        "coverage hybrid ndcg@10: 0.43 is below the baseline's 0.45"
    ]
    assert regressions(baseline, {**lines(0.75), "coverage": lines(0.45)}) == [
        "hybrid ndcg@10: 0.75 is below the baseline's 0.77"
    ]


def test_the_baseline_has_lines_for_both_sets():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["queries"] == 50
    assert baseline["coverage"]["queries"] == 12
    assert set(baseline["coverage"]) == {"queries", "lexical", "hybrid"}


def test_scores_on_one_side_of_vnni_agree_exactly():
    row = json.loads(SURVEY.read_text(encoding="utf-8"))["variants"]["paraphrase-ml-uint8"]
    for side in ("vnni", "no_vnni"):
        assert len(row[side]["gate_ndcg"]) == 1
        assert row[side]["distinct_vectors"] == 1
