"""Real pgvector round trip. Needs TEST_DATABASE_URL pointing at a database with the vector
extension available (docker compose up -d ai-db, or the pgvector service in CI)."""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL not set; needs PostgreSQL with pgvector", allow_module_level=True)

# conftest.py has already pointed DATABASE_URL at TEST_DATABASE_URL, before app.database was imported.
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import repository  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from tests.fakes import FakeEmbedder  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module", autouse=True)
def schema():
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


def test_upsert_is_idempotent_and_similarity_ranks_identical_text_first():
    embedder = FakeEmbedder()
    texts = {1: "Desert dune sunrise", 2: "Desert dune sunrise", 3: "Coastal kayak with seals"}
    with SessionLocal() as db, db.begin():
        db.execute(text("TRUNCATE tour_embeddings"))
        for tour_id, content in texts.items():
            for _ in range(2):  # redelivery: second write must not duplicate
                repository.upsert_embedding(
                    db,
                    tour_id=tour_id,
                    title=content,
                    content=content,
                    embedding=embedder.embed(content),
                    model="fake",
                    event_id=None,
                )

    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM tour_embeddings")).scalar_one() == 3
        ranked = repository.similar_to_tour(db, 1, limit=2)

    assert ranked[0][0] == 2
    assert ranked[0][1] == pytest.approx(0.0, abs=1e-6)  # identical text -> distance 0
    assert ranked[1][0] == 3
