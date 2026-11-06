"""Issue #10: evaluations record the CPU, and the gate's tolerance comes from what the survey measured."""

import json
from pathlib import Path

from eval.cpu import cpu_features
from eval.regression import TOLERANCE

SURVEY = Path(__file__).parents[1] / "eval" / "results" / "cpu_survey.json"
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


def test_scores_on_one_side_of_vnni_agree_exactly():
    row = json.loads(SURVEY.read_text(encoding="utf-8"))["variants"]["paraphrase-ml-uint8"]
    for side in ("vnni", "no_vnni"):
        assert len(row[side]["gate_ndcg"]) == 1
        assert row[side]["distinct_vectors"] == 1
