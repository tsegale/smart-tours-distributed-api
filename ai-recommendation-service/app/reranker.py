"""Optional cross-encoder re-ranking over ONNX Runtime. Off unless RERANK_MODEL is set: the evaluation decides (ADR
0020)."""

import logging
from dataclasses import dataclass
from functools import lru_cache

from .config import get_settings
from .model_files import pinned_file

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankerSpec:
    repo: str
    onnx_file: str
    revision: str
    onnx_sha256: str
    tokenizer_sha256: str
    max_length: int = 256


_MS_MARCO = ("cross-encoder/ms-marco-MiniLM-L-6-v2", "233902d25c440f23af6f7d6e94d2946bac0bee0a")
_MS_MARCO_TOKENIZER = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
_EN_DE = ("cross-encoder/msmarco-MiniLM-L6-en-de-v1", "9eb610d90d409cfefb7422e13debd405f8465af6")
_EN_DE_TOKENIZER = "96ef91dd5d1c3b157a0437e85ff1f29d0eb3cf32fd40f863dab45baa5e839fad"
_INT8 = "onnx/model_qint8_avx512_vnni.onnx"
_UINT8 = "onnx/model_quint8_avx2.onnx"

RERANKERS = {
    "ms-marco-l6-int8": RerankerSpec(
        _MS_MARCO[0],
        _INT8,
        _MS_MARCO[1],
        "3573b6b9593cb2f75987a31815d409ca3dd8808629118fd20451bb1a5d90cec7",
        _MS_MARCO_TOKENIZER,
    ),
    "ms-marco-l6-uint8": RerankerSpec(
        _MS_MARCO[0],
        _UINT8,
        _MS_MARCO[1],
        "c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9",
        _MS_MARCO_TOKENIZER,
    ),
    "en-de-l6-int8": RerankerSpec(
        _EN_DE[0],
        _INT8,
        _EN_DE[1],
        "bee1436cd1041bab174dd26d9cb1a7795ad14df79e55f8b9e4f538c96efa9b21",
        _EN_DE_TOKENIZER,
    ),
    "en-de-l6-uint8": RerankerSpec(
        _EN_DE[0],
        _UINT8,
        _EN_DE[1],
        "5d849b007fb3975d130f6f476eea9977980f5f260cbef8b0c4ba00efac67f1ab",
        _EN_DE_TOKENIZER,
    ),
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
        from tokenizers import Tokenizer

        spec = self._spec
        onnxruntime.disable_telemetry_events()
        log.info("Loading reranker %s at %s", self.name, spec.revision)
        self._session = onnxruntime.InferenceSession(
            pinned_file(spec.repo, spec.onnx_file, spec.revision, spec.onnx_sha256),
            providers=["CPUExecutionProvider"],
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(
            pinned_file(spec.repo, "tokenizer.json", spec.revision, spec.tokenizer_sha256)
        )
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
