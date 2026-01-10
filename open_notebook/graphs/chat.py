import asyncio
import concurrent.futures
import re
import sqlite3
from typing import Annotated, Callable, Coroutine, List, Optional, Tuple, TypeVar

from ai_prompter import Prompter
from langchain_core.messages import AIMessage, SystemMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict
from loguru import logger

from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.domain.notebook import Notebook
from open_notebook.graphs.image_generation import generate_image_message
from open_notebook.graphs.utils import provision_langchain_model


T = TypeVar("T")
DATA_URI_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def _run_async(coro_factory: Callable[[], Coroutine[object, object, T]]) -> T:
    """Run an async coroutine regardless of whether we're in an event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop, safe to run directly
        return asyncio.run(coro_factory())

    # We're already in an event loop (FastAPI/Starlette). Run the coroutine in
    # a worker thread with its own loop to avoid nested asyncio.run() errors.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(lambda: asyncio.run(coro_factory()))
        return future.result()


class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]
    notebook: Optional[Notebook]
    context: Optional[dict]
    context_config: Optional[dict]
    model_override: Optional[str]
    image_generation: Optional[dict]


async def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    if state.get("image_generation"):
        logger.debug("Image generation path: payload present for thread %s", config.get("configurable", {}).get("thread_id"))
        image_payload = state.get("image_generation") or {}
        planner_model_id = (
            config.get("configurable", {}).get("model_id")
            or state.get("model_override")
        )

        ai_message = await generate_image_message(
            image_request=image_payload,
            context=state.get("context"),
            planner_model_id=planner_model_id,
        )

        state["image_generation"] = None
        return {"messages": ai_message}

    system_prompt = Prompter(prompt_template="chat").render(data=state)  # type: ignore[arg-type]
    logger.debug(
        "Text chat path: thread=%s messages=%s ctx_sources=%s ctx_notes=%s model_override=%s",
        config.get("configurable", {}).get("thread_id"),
        len(state.get("messages", [])),
        len(state.get("context", {}).get("sources", [])) if state.get("context") else 0,
        len(state.get("context", {}).get("notes", [])) if state.get("context") else 0,
        state.get("model_override"),
    )
    logger.info(
        f"Text chat path: thread={config.get('configurable', {}).get('thread_id')} "
        f"messages={len(state.get('messages', []))} "
        f"image_generation={bool(state.get('image_generation'))} "
        f"keys={list(state.keys())}"
    )
    payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])
    model_id = config.get("configurable", {}).get("model_id") or state.get(
        "model_override"
    )
    combined_payload_text = str(payload)
    sanitized_text = DATA_URI_RE.sub("[image omitted]", combined_payload_text)
    model = await provision_langchain_model(
        sanitized_text,
        model_id,
        "chat",
        max_tokens=8192,
    )

    replacements = _strip_data_uris_from_messages(payload)
    try:
        ai_message = await model.ainvoke(payload)
    finally:
        _restore_data_uris(replacements)
    return {"messages": ai_message}


def _strip_data_uris_from_messages(
    messages: List[BaseMessage],
) -> List[Tuple[BaseMessage, str]]:
    replacements: List[Tuple[BaseMessage, str]] = []
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str) and "data:image" in content:
            replacements.append((message, content))
            message.content = DATA_URI_RE.sub("[image omitted]", content)
    return replacements


def _restore_data_uris(replacements: List[Tuple[BaseMessage, str]]) -> None:
    for message, original_content in replacements:
        message.content = original_content


# Use in-memory checkpointer (async-safe) to satisfy LangGraph requirements
memory = MemorySaver()
agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=memory)
