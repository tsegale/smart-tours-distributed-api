"""Text -> vector. Behind a small protocol so tests can swap in a deterministic fake."""

import logging
from functools import lru_cache
from typing import Protocol

from .config import get_settings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, dimension: int) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._model = None

    def _load(self):
        if self._model is None:
            # Imported lazily: torch is heavy, and tests never need it.
            from sentence_transformers import SentenceTransformer

            log.info("Loading embedding model %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def warm_up(self) -> None:
        self._load()

    def embed(self, text: str) -> list[float]:
        # Normalised vectors make cosine distance well behaved (and equal to 1 - dot product).
        vector = self._load().encode(text, normalize_embeddings=True)
        if len(vector) != self.dimension:
            raise ValueError(f"Model produced {len(vector)} dims, schema expects {self.dimension}")
        return vector.tolist()


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dimension)


def document_text(title: str, description: str | None) -> str:
    return f"{title}. {description}" if description else title
