import os
import jwt
import types
import pytest
from fastapi.testclient import TestClient

from api.main import app


def make_token(sub: str, email: str = "user@example.com", secret: str = "testsecret"):
    return jwt.encode(
        {"sub": sub, "email": email, "name": email, "iat": 0, "exp": 9999999999},
        secret,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "testsecret")
    monkeypatch.setenv("SKIP_MIGRATIONS_FOR_TESTS", "1")
    yield


def test_notebooks_filtered_by_owner(monkeypatch):
    # Mock repo_query to return two notebooks with different owners
    from api import routers as _
    from open_notebook.database import repository
    import api.routers.notebooks as notebooks_router

    async def fake_repo_query(query, vars=None):
        rows = [
            {"id": "notebook:1", "name": "mine", "description": "", "archived": False, "created": "2024", "updated": "2024", "owner": "user:me", "source_count": 0, "note_count": 0},
            {"id": "notebook:2", "name": "theirs", "description": "", "archived": False, "created": "2024", "updated": "2024", "owner": "user:other", "source_count": 0, "note_count": 0},
        ]
        if vars and "owner" in vars:
            want = str(vars["owner"])
            return [r for r in rows if str(r["owner"]) == want]
        return rows

    monkeypatch.setattr(repository, "repo_query", fake_repo_query)
    monkeypatch.setattr(notebooks_router, "repo_query", fake_repo_query)

    token = make_token("user:me", "me@force10partners.com")
    client = TestClient(app)
    resp = client.get("/api/notebooks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "notebook:1"


def test_source_detail_forbidden(monkeypatch):
    from open_notebook.domain import notebook

    class DummySource:
        def __init__(self, owner):
            self.id = "source:1"
            self.owner = owner
            self.title = "x"
            self.topics = []
            self.asset = None
            self.full_text = None
            self.created = "2024"
            self.updated = "2024"

        async def get_embedded_chunks(self):
            return 0

    async def fake_get(source_id):
        return DummySource(owner="user:other")

    monkeypatch.setattr(notebook.Source, "get", staticmethod(fake_get))

    token = make_token("user:me", "me@force10partners.com")
    client = TestClient(app)
    resp = client.get("/api/sources/source:1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
