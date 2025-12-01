import os
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage


class DummySession:
    def __init__(self, session_id: str):
        self.id = session_id
        self.model_override = None
        self.saved = False

    async def save(self):
        self.saved = True


def _require_gemini_key():
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return
    pytest.skip("GEMINI_API_KEY or GOOGLE_API_KEY is required for image integration tests")


@pytest.fixture
def client():
    from api.main import app

    test_client = TestClient(app)
    password = os.environ.get("OPEN_NOTEBOOK_PASSWORD")
    if password:
        test_client.headers.update({"Authorization": f"Bearer {password}"})
    return test_client


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.parametrize(
    "image_model",
    [
        {
            "id": "model:gemini-2.5-flash-image",
            "name": "gemini-2.5-flash-image",
        },
        {
            "id": "model:gemini-3-pro-image-preview",
            "name": "gemini-3-pro-image-preview",
        },
    ],
)
def test_generate_image_endpoint_returns_chat_message(monkeypatch, client, image_model):
    _require_gemini_key()

    dummy_session = DummySession("chat_session:image-test")

    async def fake_get_session(session_id: str):
        return dummy_session

    async def fake_get_model(model_id: str):
        assert model_id == image_model["id"]
        return SimpleNamespace(
            id=image_model["id"],
            name=image_model["name"],
            provider="google",
            type="image",
        )

    class FakePlanner:
        def invoke(self, messages):
            return AIMessage(
                content="""
                PLAN:
                - compose a cinematic banana scene
                FINAL PROMPT: hyperrealistic macro photo of a luminous banana astronaut exploring a misty alien canyon at golden hour, volumetric lighting, detailed textures, global illumination, dramatic perspective, richly colored nebula sky, cinematic depth of field, 85mm lens, ultra fine details, professional studio photography aesthetics, trending on artstation, ray traced reflections.
                """.strip()
            )

    async def fake_provision_langchain_model(*args, **kwargs):
        return FakePlanner()

    from api.routers import chat as chat_router
    from open_notebook.graphs.image_generation import provision_langchain_model as original_planner

    monkeypatch.setattr(chat_router.ChatSession, "get", fake_get_session)
    monkeypatch.setattr(chat_router.Model, "get", fake_get_model)
    monkeypatch.setattr(
        "open_notebook.graphs.image_generation.provision_langchain_model",
        fake_provision_langchain_model,
    )

    payload = {
        "session_id": "chat_session:image-test",
        "message": "Please create a creative banana poster",
        "context": {"sources": [], "notes": []},
        "model_override": None,
        "image_model_id": image_model["id"],
        "use_rag": False,
    }

    response = client.post("/api/chat/image", json=payload, timeout=120)
    assert response.status_code == 200, response.text

    data = response.json()
    assert "messages" in data and data["messages"], "Expected at least one AI message"

    final_message = data["messages"][-1]
    assert final_message["type"] == "ai"
    content = final_message["content"]
    assert "data:image" in content, "Image payload missing"
    assert image_model["name"] in content
    assert dummy_session.saved is True

    # restore original planner in case other tests rely on it
    monkeypatch.setattr(
        "open_notebook.graphs.image_generation.provision_langchain_model",
        original_planner,
    )
