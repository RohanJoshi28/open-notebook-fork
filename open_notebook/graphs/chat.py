import asyncio
import concurrent.futures
import sqlite3
from typing import Annotated, Callable, Coroutine, Optional, TypeVar

from ai_prompter import Prompter
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.domain.notebook import Notebook
from open_notebook.graphs.image_generation import generate_image_message
from open_notebook.graphs.utils import provision_langchain_model


T = TypeVar("T")


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


def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    if state.get("image_generation"):
        image_payload = state.get("image_generation") or {}
        planner_model_id = (
            config.get("configurable", {}).get("model_id")
            or state.get("model_override")
        )

        ai_message = _run_async(
            lambda: generate_image_message(
                image_request=image_payload,
                context=state.get("context"),
                planner_model_id=planner_model_id,
            )
        )

        state["image_generation"] = None
        return {"messages": ai_message}

    system_prompt = Prompter(prompt_template="chat").render(data=state)  # type: ignore[arg-type]
    payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])
    model_id = config.get("configurable", {}).get("model_id") or state.get(
        "model_override"
    )
    model = _run_async(
        lambda: provision_langchain_model(
            str(payload),
            model_id,
            "chat",
            max_tokens=8192,
        )
    )

    ai_message = model.invoke(payload)
    return {"messages": ai_message}


conn = sqlite3.connect(
    LANGGRAPH_CHECKPOINT_FILE,
    check_same_thread=False,
)
memory = SqliteSaver(conn)

agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=memory)
