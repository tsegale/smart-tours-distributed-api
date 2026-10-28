import hashlib
import math


class FakeEmbedder:
    """Deterministic stand-in for the sentence-transformer: same text, same unit vector."""

    model_name = "fake-embedder"
    dimension = 384

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        seed = hashlib.sha256(text.encode()).digest()
        raw = [(seed[i % len(seed)] - 128) / 128 for i in range(self.dimension)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]
