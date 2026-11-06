"""Which embedding model reads use and which ones writes go to, and the move from one to the next (ADR 0020).

Reads use the one active model. Writes go to the active model and any being backfilled, so a tour edited mid-backfill
is not left behind. The state lives in embedding_models; processes look it up every so often, so a cutover needs no
restart. A new model's first request loads it, and the old one is dropped from memory.

Migration, from docs/runbooks/embedding-model-migration.md:
    python -m app.model_registry start <model>     mark it backfilling; the worker starts writing to it too
    python -m app.model_registry backfill <model>  embed every tour that is missing or stale for it (repeatable)
    python -m app.model_registry status <model>
    python -m app.model_registry cutover <model>   flip reads, refused while any tour is missing or stale
    python -m app.model_registry purge <model>     delete a retired model's vectors
"""

import argparse
import logging
import threading
import time
from collections.abc import Callable
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from . import repository
from .config import get_settings
from .database import SessionLocal
from .embeddings import MODELS, Embedder, OnnxEmbedder, document_text

log = logging.getLogger(__name__)

REFRESH_S = 30.0
BATCH = 32

# A row that is missing, or whose text no longer matches the tour's: what still needs embedding for a model.
_OUTSTANDING = """
    FROM ai_tours t LEFT JOIN tour_embeddings e ON e.tour_id = t.tour_id AND e.model = :model
    WHERE t.name <> ''
      AND (e.tour_id IS NULL
           OR e.content IS DISTINCT FROM CASE WHEN coalesce(t.description, '') = '' THEN t.name
                                              ELSE t.name || '. ' || t.description END)
"""


class CutoverRefusedError(Exception):
    pass


def ensure_active(db: Session, default_model: str) -> None:
    """A fresh database starts on the configured model. An existing active model is left alone."""
    db.execute(
        text(
            """
            INSERT INTO embedding_models (model, state)
            SELECT :model, 'active' WHERE NOT EXISTS (SELECT 1 FROM embedding_models WHERE state = 'active')
            ON CONFLICT (model) DO UPDATE SET state = 'active', updated_at = now()
            """
        ),
        {"model": default_model},
    )


def models_in_state(db: Session, *states: str) -> list[str]:
    rows = db.execute(
        text("SELECT model FROM embedding_models WHERE state = ANY(:states) ORDER BY state, model"),
        {"states": list(states)},
    )
    return list(rows.scalars())


def start_backfill(db: Session, model: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO embedding_models (model, state) VALUES (:model, 'backfilling')
            ON CONFLICT (model) DO UPDATE SET state = 'backfilling', updated_at = now()
            WHERE embedding_models.state <> 'active'
            """
        ),
        {"model": model},
    )


def outstanding(db: Session, model: str) -> int:
    return db.execute(text(f"SELECT count(*) {_OUTSTANDING}"), {"model": model}).scalar_one()


def backfill(session_factory: sessionmaker[Session], embedder: Embedder, batch: int = BATCH) -> int:
    """Embed what's outstanding, a batch at a time, in short transactions. Reads never notice. Safe to re-run or
    interrupt."""
    done = 0
    while True:
        with session_factory() as db:
            rows = db.execute(
                text(f"SELECT t.tour_id, t.name, t.description {_OUTSTANDING} ORDER BY t.tour_id LIMIT :n"),
                {"model": embedder.model_name, "n": batch},
            ).all()
        if not rows:
            return done
        contents = [document_text(row.name, row.description) for row in rows]
        vectors = [embedder.embed_document(content) for content in contents]  # outside the transaction
        with session_factory() as db, db.begin():
            for row, content, vector in zip(rows, contents, vectors, strict=True):
                repository.upsert_embedding(
                    db,
                    tour_id=row.tour_id,
                    title=row.name,
                    content=content,
                    embedding=vector,
                    model=embedder.model_name,
                    event_id=None,
                )
        done += len(rows)
        log.info("Backfilled %d tours for %s", done, embedder.model_name)


def cut_over(db: Session, model: str) -> None:
    """In one transaction, so there is always exactly one active model. The old vectors stay, as the way back."""
    if model not in models_in_state(db, "backfilling"):
        raise CutoverRefusedError(f"{model} is not being backfilled")
    missing = outstanding(db, model)
    if missing:
        raise CutoverRefusedError(f"{missing} tours are missing or stale for {model}")
    db.execute(text("UPDATE embedding_models SET state = 'retired', updated_at = now() WHERE state = 'active'"))
    db.execute(
        text("UPDATE embedding_models SET state = 'active', updated_at = now() WHERE model = :model"), {"model": model}
    )


def purge(db: Session, model: str) -> int:
    if model not in models_in_state(db, "retired"):
        raise CutoverRefusedError(f"{model} is not retired")
    return db.execute(text("DELETE FROM tour_embeddings WHERE model = :model"), {"model": model}).rowcount


class EmbedderRegistry:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        factory: Callable[[str], Embedder],
        default_model: str,
        refresh_s: float = REFRESH_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = session_factory
        self._factory = factory
        self._default = default_model
        self._refresh_s = refresh_s
        self._clock = clock
        self._lock = threading.Lock()
        self._loaded: dict[str, Embedder] = {}
        self._active = default_model
        self._writers = [default_model]
        self._checked_at: float | None = None

    def _refresh(self) -> None:
        now = self._clock()
        if self._checked_at is not None and now - self._checked_at < self._refresh_s:
            return
        with self._sessions() as db:
            active = models_in_state(db, "active")
            backfilling = models_in_state(db, "backfilling")
        self._active = active[0] if active else self._default
        self._writers = [self._active, *backfilling]
        self._checked_at = now
        for name in set(self._loaded) - set(self._writers):
            log.info("Unloading embedding model %s", name)
            del self._loaded[name]

    def _embedder(self, name: str) -> Embedder:
        if name not in self._loaded:
            self._loaded[name] = self._factory(name)
        return self._loaded[name]

    def active(self) -> Embedder:
        with self._lock:
            self._refresh()
            return self._embedder(self._active)

    def writers(self) -> list[Embedder]:
        with self._lock:
            self._refresh()
            return [self._embedder(name) for name in self._writers]


def embedder_for(name: str) -> Embedder:
    return OnnxEmbedder(name, MODELS[name])


@lru_cache
def get_registry() -> EmbedderRegistry:
    return EmbedderRegistry(SessionLocal, embedder_for, get_settings().embedding_model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["start", "backfill", "status", "cutover", "purge"])
    parser.add_argument("model", choices=list(MODELS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.action == "backfill":
        print("embedded", backfill(SessionLocal, embedder_for(args.model)))
        return
    with SessionLocal() as db, db.begin():
        if args.action == "start":
            start_backfill(db, args.model)
        elif args.action == "cutover":
            cut_over(db, args.model)
        elif args.action == "purge":
            print("deleted", purge(db, args.model))
        print("outstanding", outstanding(db, args.model), "active", models_in_state(db, "active"))


if __name__ == "__main__":
    main()
