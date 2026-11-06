import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import get_db
from tests.fakes import FakeEmbedder


@pytest.fixture
def client(monkeypatch):
    embedder = FakeEmbedder()
    main.app.dependency_overrides[main.get_embedder] = lambda: embedder
    main.app.dependency_overrides[get_db] = lambda: None  # repository calls are faked below
    # Avoid loading the real model during lifespan startup.
    monkeypatch.setattr(main, "get_embedder", lambda: embedder)
    monkeypatch.setattr(main, "ensure_active", lambda db, model: None)
    monkeypatch.setattr(main, "get_parser", lambda: None)
    monkeypatch.setattr(main, "get_reranker", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


def test_recommend_unknown_tour_is_404(client, monkeypatch):
    monkeypatch.setattr(main.repository, "exists", lambda db, tour_id, model: False)
    response = client.get("/api/ai/recommend/99")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_recommend_returns_similarities(client, monkeypatch):
    monkeypatch.setattr(main.repository, "exists", lambda db, tour_id, model: True)
    monkeypatch.setattr(main.repository, "similar_to_tour", lambda db, tour_id, limit, model: [(7, 0.1), (3, 0.25)])

    body = client.get("/api/ai/recommend/1", params={"limit": 2}).json()

    assert body == {
        "tour_id": 1,
        "recommendations": [{"tour_id": 7, "similarity": 0.9}, {"tour_id": 3, "similarity": 0.75}],
    }


def test_recommend_limit_is_bounded(client):
    response = client.get("/api/ai/recommend/1", params={"limit": 500})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_openapi_is_served_where_the_gateway_routes_it(client):
    paths = client.get("/docs/ai/openapi.json").json()["paths"]
    assert {"/api/ai/search", "/api/ai/itineraries", "/api/ai/recommendations/me"} <= set(paths)
