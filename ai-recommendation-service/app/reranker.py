"""Optional cross-encoder re-ranking over ONNX Runtime. Off unless RERANK_MODEL is set: the evaluation decides (ADR
0020)."""

import logging
from dataclasses import dataclass
from functools import lru_cache

from .config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankerSpec:
    repo: str
    onnx_file: str
    max_length: int = 256


RERANKERS = {
    "ms-marco-l6-int8": RerankerSpec("cross-encoder/ms-marco-MiniLM-L-6-v2", "onnx/model_qint8_avx512_vnni.onnx"),
    "en-de-l6-int8": RerankerSpec("cross-encoder/msmarco-MiniLM-L6-en-de-v1", "onnx/model_qint8_avx512_vnni.onnx"),
}


class OnnxCrossEncoder:
    def __init__(self, name: str, spec: RerankerSpec) -> None:
        self.name = name
        self._spec = spec
        self._session = None
        self._tokenizer = None

    def warm_up(self) -> None:
        if self._session is not None:
            return
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        onnxruntime.disable_telemetry_events()
        log.info("Loading reranker %s", self.name)
        self._session = onnxruntime.InferenceSession(
            hf_hub_download(self._spec.repo, self._spec.onnx_file), providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(hf_hub_download(self._spec.repo, "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=self._spec.max_length)
        self._tokenizer.enable_padding()

    def score(self, query: str, documents: list[str]) -> list[float]:
        """One relevance score per document, higher is better. They only mean something relative to each other."""
        if not documents:
            return []
        import numpy as np

        self.warm_up()
        encoded = self._tokenizer.encode_batch([(query, document) for document in documents])
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64)}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
        return self._session.run(None, feed)[0][:, 0].tolist()


@lru_cache
def get_reranker() -> OnnxCrossEncoder | None:
    name = get_settings().rerank_model
    return OnnxCrossEncoder(name, RERANKERS[name]) if name else None
