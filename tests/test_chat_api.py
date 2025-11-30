from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class DummySession:
    def __init__(self):
        self.id = "chat_session:test"
        self.model_override = None
        self.saved = False

    async def save(self):
        self.saved = True


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@patch("api.routers.chat.chat_graph")
@patch("api.routers.chat.ChatSession.get", new_callable=AsyncMock)
def test_execute_chat_returns_messages(mock_get_session, mock_graph, client):
    mock_session = DummySession()
    mock_get_session.return_value = mock_session

    mock_graph.get_state.return_value = SimpleNamespace(values={"messages": []})
    mock_graph.invoke.return_value = {
        "messages": [
            SimpleNamespace(id="m1", type="ai", content="Answer"),
        ]
    }

    payload = {
        "session_id": "chat_session:test",
        "message": "Hello?",
        "context": {"sources": [], "notes": []},
        "model_override": None,
    }

    response = client.post("/api/chat/execute", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["messages"][0]["content"] == "Answer"
    assert mock_session.saved is True
    mock_graph.invoke.assert_called_once()
    call_kwargs = mock_graph.invoke.call_args.kwargs
    assert "input" in call_kwargs
    assert call_kwargs["input"]["messages"][-1].content == "Hello?"


@patch("api.routers.chat.chat_graph")
@patch("api.routers.chat.Model.get", new_callable=AsyncMock)
@patch("api.routers.chat.ChatSession.get", new_callable=AsyncMock)
def test_generate_image_sets_image_payload(mock_get_session, mock_get_model, mock_graph, client):
    mock_session = DummySession()
    mock_get_session.return_value = mock_session

    mock_get_model.return_value = SimpleNamespace(
        id="model:nano",
        name="nanobanana-pro",
        provider="google",
        type="image",
    )

    mock_graph.get_state.return_value = SimpleNamespace(values={"messages": []})
    mock_graph.invoke.return_value = {
        "messages": [
            SimpleNamespace(id="img1", type="ai", content="![img](data:image/png;base64,AAA=)")
        ]
    }

    payload = {
        "session_id": "chat_session:test",
        "message": "Paint a banana",
        "context": {"sources": [{"title": "Doc", "insights": []}], "notes": []},
        "model_override": "model:text",
        "image_model_id": "model:nano",
        "use_rag": False,
    }

    response = client.post("/api/chat/image", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["messages"][0]["type"] == "ai"

    mock_graph.invoke.assert_called_once()
    state_values = mock_graph.invoke.call_args.kwargs["input"]
    assert state_values["image_generation"]["image_model"]["name"] == "nanobanana-pro"
    assert state_values["context"] == {"sources": [], "notes": []}
