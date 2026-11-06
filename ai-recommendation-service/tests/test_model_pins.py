"""Every model file is pinned to an exact revision and hash, and config can't quietly disagree (ADR 0020)."""

import hashlib
import re
from pathlib import Path

import pytest

from app import model_files
from app.config import Settings
from app.embeddings import MODELS
from app.model_files import ModelFileChangedError, pinned_file
from app.model_registry import RevisionMismatchError, check_configured_revisions
from app.reranker import RERANKERS

COMPOSE = Path(__file__).parents[2] / "docker-compose.yml"
SHIPPED = "paraphrase-ml-uint8"
REVISION = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


@pytest.mark.parametrize("spec", [*MODELS.values(), *RERANKERS.values()], ids=lambda spec: spec.repo + spec.onnx_file)
def test_every_model_is_pinned(spec):
    assert REVISION.fullmatch(spec.revision)
    assert SHA256.fullmatch(spec.onnx_sha256)
    assert SHA256.fullmatch(spec.tokenizer_sha256)


def test_the_shipped_files_hash_is_the_one_the_survey_measured():
    spec = MODELS[SHIPPED]
    assert spec.onnx_file == "onnx/model_quint8_avx2.onnx"
    assert spec.onnx_sha256 == "98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f"


@pytest.fixture
def cached_file(tmp_path, monkeypatch):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"the evaluated bytes")
    seen = {}

    def download(repo, filename, revision):
        seen.update(repo=repo, filename=filename, revision=revision)
        return str(path)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)
    return path, seen


def test_a_matching_file_loads_at_the_pinned_revision(cached_file):
    path, seen = cached_file
    expected = hashlib.sha256(b"the evaluated bytes").hexdigest()
    assert pinned_file("org/model", "model.onnx", "abc", expected) == str(path)
    assert seen == {"repo": "org/model", "filename": "model.onnx", "revision": "abc"}


def test_a_changed_file_is_refused(cached_file):
    path, _ = cached_file
    expected = model_files.sha256_of(str(path))
    path.write_bytes(b"different bytes")
    with pytest.raises(ModelFileChangedError, match="expected " + expected):
        pinned_file("org/model", "model.onnx", "abc", expected)


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_config_that_agrees_with_code_or_says_nothing_starts():
    check_configured_revisions(settings(embedding_model=SHIPPED, embedding_model_revision=""))
    check_configured_revisions(settings(embedding_model=SHIPPED, embedding_model_revision=MODELS[SHIPPED].revision))


def test_config_that_disagrees_with_code_stops_the_start():
    with pytest.raises(RevisionMismatchError):
        check_configured_revisions(settings(embedding_model=SHIPPED, embedding_model_revision="0" * 40))
    with pytest.raises(RevisionMismatchError):
        check_configured_revisions(
            settings(embedding_model=SHIPPED, rerank_model="en-de-l6-uint8", rerank_model_revision="0" * 40)
        )


def test_compose_states_the_revision_pinned_in_code():
    compose = COMPOSE.read_text(encoding="utf-8")
    default = re.search(r"EMBEDDING_MODEL: \$\{EMBEDDING_MODEL:-(.+)\}", compose).group(1)
    revision = re.search(r"EMBEDDING_MODEL_REVISION: \$\{EMBEDDING_MODEL_REVISION:-(.+)\}", compose).group(1)
    assert default == SHIPPED
    assert revision == MODELS[default].revision
