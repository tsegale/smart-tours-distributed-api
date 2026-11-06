"""Issue #10: evaluations record the CPU, and the gate says what a vector failure might mean."""

from eval.cpu import cpu_features
from eval.regression import drift_notice


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


def test_a_vector_failure_is_labelled_and_links_the_issue():
    message, annotation = drift_notice(["hybrid ndcg@10: 0.75 is below the baseline's 0.76"])
    assert "possible CPU-dependent int8 drift; see issue" in message
    assert message.endswith("/issues/10")
    assert annotation.startswith("::error ")
    assert message in annotation


def test_a_lexical_failure_is_not_blamed_on_the_cpu():
    assert drift_notice(["lexical ndcg@10: 0.40 is below the baseline's 0.45"]) == []
    assert drift_notice([]) == []
