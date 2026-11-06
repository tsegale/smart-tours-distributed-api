"""Text -> vector. Behind a small protocol so tests can swap in a deterministic fake."""

import logging
from dataclasses import dataclass
from typing import Protocol

from .model_files import pinned_file

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed_document(self, text: str) -> list[float]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """PyTorch, for the runtime comparison in eval/footprint.py only: not in the runtime image (ADR 0020)."""

    def __init__(
        self,
        model_name: str,
        dimension: int,
        query_prefix: str = "",
        document_prefix: str = "",
        revision: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._revision = revision
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._model = None

    def warm_up(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("Loading embedding model %s", self.model_name)
            self._model = SentenceTransformer(self.model_name, revision=self._revision)

    def embed_document(self, text: str) -> list[float]:
        return self._embed(self._document_prefix + text)

    def embed_query(self, text: str) -> list[float]:
        return self._embed(self._query_prefix + text)

    def _embed(self, text: str) -> list[float]:
        self.warm_up()
        vector = self._model.encode(text, normalize_embeddings=True)
        if len(vector) != self.dimension:
            raise ValueError(f"Model produced {len(vector)} dims, schema expects {self.dimension}")
        return vector.tolist()


@dataclass(frozen=True)
class ModelSpec:
    """What it takes to run a model. The prefixes are part of the model: e5 was trained with them. The revision and the
    hashes pin the exact bytes that were evaluated (app/model_files.py)."""

    repo: str
    onnx_file: str
    revision: str
    onnx_sha256: str | None
    tokenizer_sha256: str | None
    query_prefix: str = ""
    document_prefix: str = ""
    max_length: int = 256


_MINILM = ("sentence-transformers/all-MiniLM-L6-v2", "1110a243fdf4706b3f48f1d95db1a4f5529b4d41")
_MINILM_TOKENIZER = "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"
_PARAPHRASE = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
)
_PARAPHRASE_TOKENIZER = "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"
_E5 = ("intfloat/multilingual-e5-small", "614241f622f53c4eeff9890bdc4f31cfecc418b3")
_E5_TOKENIZER = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"


def _spec(pin: tuple[str, str], onnx_file: str, onnx_sha256: str, tokenizer_sha256: str, **options) -> ModelSpec:
    return ModelSpec(pin[0], onnx_file, pin[1], onnx_sha256, tokenizer_sha256, **options)


MODELS = {
    "minilm-l6": _spec(
        _MINILM,
        "onnx/model.onnx",
        "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
        _MINILM_TOKENIZER,
    ),
    "minilm-l6-int8": _spec(
        _MINILM,
        "onnx/model_qint8_avx512_vnni.onnx",
        "4278337fd0ff3c68bfb6291042cad8ab363e1d9fbc43dcb499fe91c871902474",
        _MINILM_TOKENIZER,
    ),
    "bge-small-en": _spec(
        ("BAAI/bge-small-en-v1.5", "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"),
        "onnx/model.onnx",
        "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        query_prefix="Represent this sentence for searching relevant passages: ",
        max_length=512,
    ),
    "paraphrase-ml": _spec(
        _PARAPHRASE,
        "onnx/model.onnx",
        "10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a",
        _PARAPHRASE_TOKENIZER,
        max_length=128,
    ),
    "paraphrase-ml-int8": _spec(
        _PARAPHRASE,
        "onnx/model_qint8_avx512_vnni.onnx",
        "783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672",
        _PARAPHRASE_TOKENIZER,
        max_length=128,
    ),
    # What runs. U8U8 weights, because the int8 file above loses quality on a CPU without VNNI: issue #10, ADR 0020.
    "paraphrase-ml-uint8": _spec(
        _PARAPHRASE,
        "onnx/model_quint8_avx2.onnx",
        "98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f",
        _PARAPHRASE_TOKENIZER,
        max_length=128,
    ),
    "e5-small": _spec(
        _E5,
        "onnx/model.onnx",
        "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
        _E5_TOKENIZER,
        query_prefix="query: ",
        document_prefix="passage: ",
        max_length=512,
    ),
    "e5-small-int8": _spec(
        _E5,
        "onnx/model_qint8_avx512_vnni.onnx",
        "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
        _E5_TOKENIZER,
        query_prefix="query: ",
        document_prefix="passage: ",
        max_length=512,
    ),
}


class OnnxEmbedder:
    """Mean pooling over ONNX Runtime and a Rust tokenizer: the same maths as sentence-transformers, without torch."""

    def __init__(self, name: str, spec: ModelSpec, threads: int = 0) -> None:
        self.model_name = name
        self.dimension = 384
        self._spec = spec
        self._threads = threads
        self._session = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime
        from tokenizers import Tokenizer

        spec = self._spec
        onnxruntime.disable_telemetry_events()
        log.info("Loading %s (%s at %s)", self.model_name, spec.onnx_file, spec.revision)
        model_path = pinned_file(spec.repo, spec.onnx_file, spec.revision, spec.onnx_sha256)
        options = onnxruntime.SessionOptions()
        if self._threads:
            options.intra_op_num_threads = self._threads
        self._session = onnxruntime.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(
            pinned_file(spec.repo, "tokenizer.json", spec.revision, spec.tokenizer_sha256)
        )
        self._tokenizer.enable_truncation(max_length=self._spec.max_length)
        self._tokenizer.enable_padding()

    def warm_up(self) -> None:
        self._load()

    def embed_batch(self, texts: list[str], prefix: str = "") -> list[list[float]]:
        import numpy as np

        self._load()
        encoded = self._tokenizer.encode_batch([prefix + t for t in texts])
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-9, None)).tolist()

    def embed_document(self, text: str) -> list[float]:
        return self.embed_batch([text], self._spec.document_prefix)[0]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_batch([text], self._spec.query_prefix)[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_batch(texts, self._spec.document_prefix)


def document_text(title: str, description: str | None) -> str:
    return f"{title}. {description}" if description else title
