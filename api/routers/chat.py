import asyncio
from typing import Any, Dict, Iterable, List, Optional
import os

from fastapi import APIRouter, HTTPException, Query, Depends
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.models import Model
from open_notebook.domain.notebook import ChatSession, Note, Notebook, Source
from open_notebook.exceptions import (
    NotFoundError,
)
from open_notebook.graphs.chat import graph as chat_graph
from open_notebook.graphs.image_generation import generate_image_message
from open_notebook.utils import render_message_content
from api.deps import get_current_user_id

router = APIRouter()


async def _ensure_notebook_owned(notebook_id: str, user_id: str) -> Notebook:
    notebook = await Notebook.get(notebook_id)
    if not notebook or str(notebook.owner) != str(user_id):
        raise HTTPException(status_code=404, detail="Notebook not found")
    return notebook


async def _ensure_session_owned(session_id: str, user_id: str) -> ChatSession:
    full_session_id = (
        session_id if session_id.startswith("chat_session:") else f"chat_session:{session_id}"
    )
    session = await ChatSession.get(full_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Backward compatibility: older sessions may not have an owner set.
    session_owner = getattr(session, "owner", None)
    if session_owner is None:
        try:
            session.owner = user_id  # type: ignore[attr-defined]
            await session.save()
        except Exception:
            pass
    elif str(session_owner) != str(user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return session

# Request/Response models
class CreateSessionRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID to create session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatMessage(BaseModel):
    id: str = Field(..., description="Message ID")
    type: str = Field(..., description="Message type (human|ai)")
    content: str = Field(..., description="Message content")
    timestamp: Optional[str] = Field(None, description="Message timestamp")

    @field_validator("content", mode="before")
    @classmethod
    def _ensure_string_content(cls, value: Any) -> str:
        """Normalize structured message payloads (e.g., Gemini parts) to plain text."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return render_message_content(value)
        except Exception:
            return str(value)


class ChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    notebook_id: Optional[str] = Field(None, description="Notebook ID")
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionWithMessagesResponse(ChatSessionResponse):
    messages: List[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )


class ExecuteChatRequest(BaseModel):
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., description="User message content")
    context: Dict[str, Any] = Field(
        ..., description="Chat context with sources and notes"
    )
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )


class ExecuteChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    messages: List[ChatMessage] = Field(..., description="Updated message list")


class GenerateImageRequest(BaseModel):
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., description="User prompt for image generation")
    context: Dict[str, Any] = Field(
        default_factory=lambda: {"sources": [], "notes": []},
        description="Optional RAG context",
    )
    model_override: Optional[str] = Field(
        None, description="Optional planner model override"
    )
    image_model_id: str = Field(..., description="Image model ID (must be type=image)")
    use_rag: bool = Field(False, description="Whether to include RAG context")


class BuildContextRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID")
    context_config: Dict[str, Any] = Field(..., description="Context configuration")


class BuildContextResponse(BaseModel):
    context: Dict[str, Any] = Field(..., description="Built context data")
    token_count: int = Field(..., description="Estimated token count")
    char_count: int = Field(..., description="Character count")


class SuccessResponse(BaseModel):
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")


@router.get("/chat/sessions", response_model=List[ChatSessionResponse])
async def get_sessions(
    notebook_id: str = Query(..., description="Notebook ID"),
    user_id: str = Depends(get_current_user_id),
):
    """Get all chat sessions for a notebook."""
    try:
        notebook = await _ensure_notebook_owned(notebook_id, user_id)
        sessions = [
            s for s in await notebook.get_chat_sessions()
            if str(s.owner) == str(user_id)
        ]

        responses: list[ChatSessionResponse] = []
        for session in sessions:
            # Pull message history from chat_graph state to avoid "missing" messages (e.g., image replies)
            state = chat_graph.get_state(
                config=RunnableConfig(configurable={"thread_id": session.id})
            )
            messages = []
            if state and state.values:
                messages = list(state.values.get("messages", []))

            responses.append(
                ChatSessionResponse(
                    id=session.id or "",
                    title=session.title or "Untitled Session",
                    notebook_id=notebook_id,
                    created=str(session.created),
                    updated=str(session.updated),
                    message_count=len(messages),
                    model_override=getattr(session, "model_override", None),
                )
            )

        return responses
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except Exception as e:
        logger.error(f"Error fetching chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat sessions: {str(e)}"
        )


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_session(request: CreateSessionRequest, user_id: str = Depends(get_current_user_id)):
    """Create a new chat session."""
    try:
        # Verify notebook exists
        notebook = await _ensure_notebook_owned(request.notebook_id, user_id)

        # Create new session
        session = ChatSession(
            title=request.title or f"Chat Session {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
            owner=user_id,
        )
        await session.save()

        # Relate session to notebook
        await session.relate_to_notebook(request.notebook_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=request.notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=0,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except Exception as e:
        logger.error(f"Error creating chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating chat session: {str(e)}"
        )


@router.get(
    "/chat/sessions/{session_id}", response_model=ChatSessionWithMessagesResponse
)
async def get_session(session_id: str, user_id: str = Depends(get_current_user_id)):
    """Get a specific session with its messages."""
    try:
        # Get session
        session = await _ensure_session_owned(session_id, user_id)
        logger.info("get_session: id=%s user=%s", session_id, user_id)

        # Get session state from LangGraph to retrieve messages
        thread_state = chat_graph.get_state(
            config=RunnableConfig(configurable={"thread_id": session_id})
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        if thread_state and thread_state.values and "messages" in thread_state.values:
            for msg in thread_state.values["messages"]:
                messages.append(
                    ChatMessage(
                        id=getattr(msg, "id", f"msg_{len(messages)}"),
                        type=msg.type if hasattr(msg, "type") else "unknown",
                        content=_render_message(msg),
                        timestamp=None,  # LangChain messages don't have timestamps by default
                    )
                )
        logger.debug("get_session: recovered %s messages from state", len(messages))

        # Find notebook_id (we need to query the relationship)
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )

        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )

        notebook_id = notebook_query[0]["out"] if notebook_query else None

        if not notebook_id:
            # This might be an old session created before API migration
            logger.warning(
                f"No notebook relationship found for session {session_id} - may be an orphaned session"
            )

        return ChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=len(messages),
            messages=messages,
            model_override=getattr(session, "model_override", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        logger.error(f"Error fetching session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching session: {str(e)}")


@router.put("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(session_id: str, request: UpdateSessionRequest, user_id: str = Depends(get_current_user_id)):
    """Update session title."""
    try:
        session = await _ensure_session_owned(session_id, user_id)

        update_data = request.model_dump(exclude_unset=True)

        if "title" in update_data:
            session.title = update_data["title"]

        if "model_override" in update_data:
            session.model_override = update_data["model_override"]

        await session.save()

        # Find notebook_id
        # Ensure session_id has proper table prefix
        full_session_id = (
            session_id
            if session_id.startswith("chat_session:")
            else f"chat_session:{session_id}"
        )
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook_id = notebook_query[0]["out"] if notebook_query else None

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=0,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        logger.error(f"Error updating session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating session: {str(e)}")


@router.delete("/chat/sessions/{session_id}", response_model=SuccessResponse)
async def delete_session(session_id: str, user_id: str = Depends(get_current_user_id)):
    """Delete a chat session."""
    try:
        session = await _ensure_session_owned(session_id, user_id)

        await session.delete()

        return SuccessResponse(success=True, message="Session deleted successfully")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting session: {str(e)}")


@router.post("/chat/execute", response_model=ExecuteChatResponse)
async def execute_chat(request: ExecuteChatRequest, user_id: str = Depends(get_current_user_id)):
    """Execute a chat request and get AI response."""
    try:
        # Verify session exists
        session = await _ensure_session_owned(request.session_id, user_id)
        logger.info(
            "chat_execute: session=%s notebook=%s model_override=%s ctx_sources=%s ctx_notes=%s",
            request.session_id,
            getattr(session, "notebook_id", None),
            request.model_override,
            len(request.context.get("sources", [])) if request.context else 0,
            len(request.context.get("notes", [])) if request.context else 0,
        )

        # Determine model override (per-request override takes precedence over session-level)
        model_override = (
            request.model_override
            if request.model_override is not None
            else getattr(session, "model_override", None)
        )

        # Get current state
        current_state = chat_graph.get_state(
            config=RunnableConfig(
                configurable={"thread_id": request.session_id}
            )
        )

        # Prepare state for execution
        state_values = current_state.values if current_state else {}
        state_values["messages"] = state_values.get("messages", [])
        state_values["context"] = request.context
        state_values["model_override"] = model_override
        state_values["image_generation"] = None

        # Add user message to state
        user_message = HumanMessage(content=request.message)
        state_values["messages"].append(user_message)

        # Primary path: call chat_graph with timeout; surface errors instead of silent fallback
        import asyncio

        timeout_s = int(os.getenv("CHAT_TIMEOUT_SECONDS", "40"))
        try:
            result = await asyncio.wait_for(
                chat_graph.ainvoke(  # type: ignore[arg-type]
                    input=state_values,
                    config=RunnableConfig(
                        configurable={
                            "thread_id": request.session_id,
                            "model_id": model_override,
                        }
                    ),
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.error(f"Chat graph timed out after {timeout_s}s")
            raise HTTPException(status_code=504, detail="Chat generation timed out. Please retry.")
        except Exception as e:
            logger.error(f"Chat graph failed: {e}")
            raise HTTPException(status_code=500, detail=f"Chat generation failed: {e}")

        # Persist messages back into graph state so subsequent fetches see them
        chat_graph.update_state(
            config=RunnableConfig(configurable={"thread_id": request.session_id}),
            values={"messages": result.get("messages", [])},
        )
        await session.save()

        messages: list[ChatMessage] = []
        for msg in result.get("messages", []):
            msg_id = getattr(msg, "id", None) or f"msg_{len(messages)}"
            messages.append(
                ChatMessage(
                    id=msg_id,
                    type=msg.type if hasattr(msg, "type") else "unknown",
                    content=_render_message(msg),
                    timestamp=None,
                )
            )

        return ExecuteChatResponse(session_id=request.session_id, messages=messages)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        logger.error(f"Error executing chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error executing chat: {str(e)}")


@router.post("/chat/image", response_model=ExecuteChatResponse)
async def generate_image(request: GenerateImageRequest):
    """Generate an image via Nano Banana models and store messages in the chat session."""
    try:
        logger.info(
            "chat_image: session=%s model_override=%s image_model_id=%s use_rag=%s ctx_sources=%s ctx_notes=%s",
            request.session_id,
            request.model_override,
            request.image_model_id,
            request.use_rag,
            len(request.context.get("sources", [])) if request.context else 0,
            len(request.context.get("notes", [])) if request.context else 0,
        )
        full_session_id = (
            request.session_id
            if request.session_id.startswith("chat_session:")
            else f"chat_session:{request.session_id}"
        )
        session = await ChatSession.get(full_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        image_model_id = (
            request.image_model_id
            if request.image_model_id.startswith("model:")
            else f"model:{request.image_model_id}"
        )
        image_model = await Model.get(image_model_id)
        if not image_model:
            raise HTTPException(status_code=404, detail="Image model not found")
        if image_model.type != "image":
            raise HTTPException(
                status_code=400, detail="Selected model is not configured as an image model"
            )

        model_override = (
            request.model_override
            if request.model_override is not None
            else getattr(session, "model_override", None)
        )

        current_state = chat_graph.get_state(
            config=RunnableConfig(configurable={"thread_id": request.session_id})
        )
        state_values = current_state.values if current_state else {}
        state_values["messages"] = state_values.get("messages", [])
        state_values["context"] = request.context if request.use_rag else {"sources": [], "notes": []}
        state_values["model_override"] = model_override
        state_values["image_generation"] = {
            "image_prompt": request.message,
            "use_rag": request.use_rag,
            "image_model": {
                "id": image_model.id,
                "name": image_model.name,
                "provider": image_model.provider,
            },
        }
        logger.info(
            f"Submitting image generation for session={request.session_id} model={image_model.name} rag={request.use_rag} ctx_sources={len(state_values['context'].get('sources', []))}"
        )
        logger.debug(
            "Prepared image_generation payload model=%s prompt_len=%s rag_context_counts=(%s sources, %s notes)",
            image_model.name,
            len(request.message),
            len(state_values["context"].get("sources", [])),
            len(state_values["context"].get("notes", [])),
        )

        user_message = HumanMessage(content=request.message)
        state_values["messages"].append(user_message)

        # Directly invoke image generation to avoid state machine dropping the flag
        ai_image_message = await generate_image_message(
            image_request=state_values["image_generation"],  # type: ignore[arg-type]
            context=state_values.get("context"),
            planner_model_id=model_override,
        )
        logger.debug(
            "chat_image: generated image message id=%s len=%s",
            getattr(ai_image_message, "id", None),
            len(ai_image_message.content or ""),
        )

        # Build message history manually (human + image response)
        messages_result = state_values["messages"] + [ai_image_message]
        result = {"messages": messages_result}

        # Persist messages to graph state so they are returned on subsequent fetches
        chat_graph.update_state(
            config=RunnableConfig(configurable={"thread_id": request.session_id}),
            values={"messages": messages_result},
        )

        await session.save()

        messages: list[ChatMessage] = []
        for msg in result.get("messages", []):
            msg_id = getattr(msg, "id", None) or f"msg_{len(messages)}"
            messages.append(
                ChatMessage(
                    id=msg_id,
                    type=msg.type if hasattr(msg, "type") else "unknown",
                    content=_render_message(msg),
                    timestamp=None,
                )
            )

        return ExecuteChatResponse(session_id=request.session_id, messages=messages)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error generating image for session {}: {}", request.session_id, e)
        raise HTTPException(status_code=500, detail=f"Error generating image: {str(e)}")


@router.post("/chat/context", response_model=BuildContextResponse)
async def build_context(request: BuildContextRequest, user_id: str = Depends(get_current_user_id)):
    """Build context for a notebook based on context configuration."""
    try:
        # Verify notebook exists
        notebook = await _ensure_notebook_owned(request.notebook_id, user_id)

        context_data: dict[str, list[dict[str, str]]] = {"sources": [], "notes": []}
        total_content = ""

        # Process context configuration if provided
        if request.context_config:
            # Process sources
            for source_id, status in request.context_config.get("sources", {}).items():
                if "not in" in status:
                    continue

                try:
                    # Add table prefix if not present
                    full_source_id = (
                        source_id
                        if source_id.startswith("source:")
                        else f"source:{source_id}"
                    )

                    try:
                        source = await Source.get(full_source_id)
                    except Exception:
                        continue
                    if not source or str(source.owner) != str(user_id):
                        continue

                    if "insights" in status:
                        source_context = await source.get_context(context_size="short")
                        context_data["sources"].append(source_context)
                        total_content += str(source_context)
                    elif "full content" in status:
                        source_context = await source.get_context(context_size="long")
                        context_data["sources"].append(source_context)
                        total_content += str(source_context)
                except Exception as e:
                    logger.warning(f"Error processing source {source_id}: {str(e)}")
                    continue

            # Process notes
            for note_id, status in request.context_config.get("notes", {}).items():
                if "not in" in status:
                    continue

                try:
                    # Add table prefix if not present
                    full_note_id = (
                        note_id if note_id.startswith("note:") else f"note:{note_id}"
                    )
                    note = await Note.get(full_note_id)
                    if not note or str(note.owner) != str(user_id):
                        continue

                    if "full content" in status:
                        note_context = note.get_context(context_size="long")
                        context_data["notes"].append(note_context)
                        total_content += str(note_context)
                except Exception as e:
                    logger.warning(f"Error processing note {note_id}: {str(e)}")
                    continue
        else:
            # Default behavior - include all sources and notes with full context so RAG has real content
            sources = await notebook.get_sources()
            for source in sources:
                try:
                    full_source = await Source.get(source.id) or source
                    source_context = await full_source.get_context(context_size="long")
                    context_data["sources"].append(source_context)
                    total_content += str(source_context)
                except Exception as e:
                    logger.warning(f"Error processing source {source.id}: {str(e)}")
                    continue

            notes = await notebook.get_notes()
            for note in notes:
                try:
                    full_note = await Note.get(note.id) or note
                    note_context = full_note.get_context(context_size="long")
                    context_data["notes"].append(note_context)
                    total_content += str(note_context)
                except Exception as e:
                    logger.warning(f"Error processing note {note.id}: {str(e)}")
                    continue

        # Calculate character and token counts
        char_count = len(total_content)
        # Use token count utility if available
        try:
            from open_notebook.utils import token_count

            estimated_tokens = token_count(total_content) if total_content else 0
        except ImportError:
            # Fallback to simple estimation
            estimated_tokens = char_count // 4

        return BuildContextResponse(
            context=context_data, token_count=estimated_tokens, char_count=char_count
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building context: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error building context: {str(e)}")


def _iter_messages(raw_messages: Any) -> Iterable[Any]:
    """LangGraph can return a single message or a list; normalize to an iterable."""
    if raw_messages is None:
        return []
    if isinstance(raw_messages, BaseMessage):
        return [raw_messages]
    if isinstance(raw_messages, list):
        return raw_messages
    if isinstance(raw_messages, tuple):
        return list(raw_messages)
    return [raw_messages]


def _render_message(msg: Any) -> str:
    """
    Normalize LangChain/Gemini message payloads into plain text for responses.
    Some providers return structured content (lists of parts, inline data, etc.)
    and FastAPI's response validation requires that we always emit strings.
    """
    if isinstance(msg, dict) and "content" in msg:
        content = msg["content"]
    else:
        content = getattr(msg, "content", msg)
    try:
        rendered = render_message_content(content)
    except Exception:
        rendered = str(content)
    return rendered if isinstance(rendered, str) else str(rendered)
