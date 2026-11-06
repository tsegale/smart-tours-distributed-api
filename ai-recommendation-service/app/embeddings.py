"""Text -> vector. Behind a small protocol so tests can swap in a deterministic fake."""

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed_document(self, text: str) -> list[float]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """PyTorch, for the runtime comparison in eval/footprint.py only: not in the runtime image (ADR 0020)."""

    def __init__(self, model_name: str, dimension: int, query_prefix: str = "", document_prefix: str = "") -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._model = None

    def warm_up(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("Loading embedding model %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)

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
    """What it takes to run a model. The prefixes are part of the model: e5 was trained with them."""

    repo: str
    onnx_file: str
    query_prefix: str = ""
    document_prefix: str = ""
    max_length: int = 256


MODELS = {
    "minilm-l6": ModelSpec("sentence-transformers/all-MiniLM-L6-v2", "onnx/model.onnx"),
    "minilm-l6-int8": ModelSpec("sentence-transformers/all-MiniLM-L6-v2", "onnx/model_qint8_avx512_vnni.onnx"),
    "bge-small-en": ModelSpec(
        "BAAI/bge-small-en-v1.5",
        "onnx/model.onnx",
        query_prefix="Represent this sentence for searching relevant passages: ",
        max_length=512,
    ),
    "paraphrase-ml": ModelSpec(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "onnx/model.onnx", max_length=128
    ),
    "paraphrase-ml-int8": ModelSpec(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "onnx/model_qint8_avx512_vnni.onnx",
        max_length=128,
    ),
    "e5-small": ModelSpec(
        "intfloat/multilingual-e5-small",
        "onnx/model.onnx",
        query_prefix="query: ",
        document_prefix="passage: ",
        max_length=512,
    ),
    "e5-small-int8": ModelSpec(
        "intfloat/multilingual-e5-small",
        "onnx/model_qint8_avx512_vnni.onnx",
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
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        onnxruntime.disable_telemetry_events()
        log.info("Loading %s (%s)", self.model_name, self._spec.onnx_file)
        model_path = hf_hub_download(self._spec.repo, self._spec.onnx_file)
        options = onnxruntime.SessionOptions()
        if self._threads:
            options.intra_op_num_threads = self._threads
        self._session = onnxruntime.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(hf_hub_download(self._spec.repo, "tokenizer.json"))
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
