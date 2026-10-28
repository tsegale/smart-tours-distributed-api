import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import get_db
from app.embeddings import get_embedder
from tests.fakes import FakeEmbedder


@pytest.fixture
def client(monkeypatch):
    embedder = FakeEmbedder()
    main.app.dependency_overrides[get_embedder] = lambda: embedder
    main.app.dependency_overrides[get_db] = lambda: None  # repository calls are faked below
    # Avoid loading the real model during lifespan startup.
    monkeypatch.setattr(main, "get_embedder", lambda: embedder)
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


def test_recommend_unknown_tour_is_404(client, monkeypatch):
    monkeypatch.setattr(main.repository, "exists", lambda db, tour_id: False)
    assert client.get("/api/ai/recommend/99").status_code == 404


def test_recommend_returns_similarities(client, monkeypatch):
    monkeypatch.setattr(main.repository, "exists", lambda db, tour_id: True)
    monkeypatch.setattr(main.repository, "similar_to_tour", lambda db, tour_id, limit: [(7, 0.1), (3, 0.25)])

    body = client.get("/api/ai/recommend/1", params={"limit": 2}).json()

    assert body == {
        "tour_id": 1,
        "recommendations": [{"tour_id": 7, "similarity": 0.9}, {"tour_id": 3, "similarity": 0.75}],
    }


def test_recommend_limit_is_bounded(client):
    assert client.get("/api/ai/recommend/1", params={"limit": 500}).status_code == 422


def test_semantic_search_embeds_the_query(client, monkeypatch):
    seen = {}

    def similar_to_vector(db, vector, limit):
        seen["dims"], seen["limit"] = len(vector), limit
        return [(5, 0.2)]

    monkeypatch.setattr(main.repository, "similar_to_vector", similar_to_vector)

    body = client.get("/api/ai/search", params={"q": "sunset photography, no long hikes"}).json()

    assert body["results"] == [{"tour_id": 5, "similarity": 0.8}]
    assert seen == {"dims": 384, "limit": 5}
