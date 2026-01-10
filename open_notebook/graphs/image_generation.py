from __future__ import annotations

import textwrap
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger

from open_notebook.graphs.utils import provision_langchain_model
from open_notebook.services.nano_banana import (
    NanoBananaError,
    generate_nano_banana_image,
)
from open_notebook.utils import render_message_content


def _build_context_summary(context: Optional[dict], max_sections: int = 5) -> str:
    if not context or not isinstance(context, dict):
        return ""

    sections: list[str] = []

    sources = context.get("sources") or []
    for idx, source in enumerate(sources[: max_sections // 2 or 1], start=1):
        title = source.get("title") or source.get("id") or f"Source {idx}"
        insights = source.get("insights") or []
        snippet = ""
        if source.get("full_text"):
            snippet = str(source["full_text"])[:800]
        elif insights:
            snippet = "\n".join(
                f"- {insight.get('insight_type', 'Insight')}: {insight.get('content', '')}"
                for insight in insights[:2]
            )
        sections.append(f"[Source] {title}\n{snippet}".strip())

    notes = context.get("notes") or []
    for idx, note in enumerate(notes[: max_sections - len(sections)], start=1):
        title = note.get("title") or f"Note {idx}"
        content = (note.get("content") or "")[:600]
        sections.append(f"[Note] {title}\n{content}".strip())

    summary = "\n\n".join(filter(None, sections))
    return summary


def _planner_system_prompt() -> str:
    return textwrap.dedent(
        """
        You are an image prompt planner whose top priority is fidelity.

        Rules:
        - Capture EVERY element from the user request exactly as written (subjects, actions, styles, constraints).
        - If RAG passages are provided, quote or paraphrase only the facts that matter; never invent new story beats.
        - You may clarify missing technical details (camera, lighting, aspect ratio) but keep them neutral and non-narrative.
        - Do NOT add new characters, props, moods, or story twists that were not present in the prompt or passages.

        Output format:
        1. "Request Summary" – short bullet list restating the user instructions verbatim.
        2. "Context Highlights" – bullet list of essential facts pulled from the passages (omit if no context).
        3. Optional "Technical Notes" – camera/lighting/composition guidance inferred from the request.
        4. "FINAL PROMPT:" followed by a single coherent paragraph that stitches the above details together without adding anything new.
        """
    ).strip()


def _human_planner_prompt(user_prompt: str, context_summary: str) -> str:
    if context_summary:
        return textwrap.dedent(
            f"""
            User request:
            {user_prompt}

            Relevant notebook passages:
            {context_summary}

            Provide the detailed plan plus FINAL PROMPT.
            """
        ).strip()
    return textwrap.dedent(
        f"""
        User request:
        {user_prompt}

        Provide the detailed plan plus FINAL PROMPT.
        """
    ).strip()


def _extract_final_prompt(plan_text: str) -> str:
    marker = "FINAL PROMPT:"
    lowered = plan_text.upper()
    idx = lowered.rfind(marker)
    if idx == -1:
        return plan_text.strip()
    start = idx + len(marker)
    return plan_text[start:].strip()


async def generate_image_message(
    image_request: Dict[str, Any],
    context: Optional[Dict[str, Any]],
    planner_model_id: Optional[str],
) -> AIMessage:
    context_summary = ""
    if image_request.get("use_rag"):
        context_summary = _build_context_summary(context)

    logger.debug(
        "Image planner start model={} use_rag={} prompt_len={} context_len={}",
        image_request["image_model"]["name"],
        image_request.get("use_rag"),
        len(image_request["image_prompt"]),
        len(context_summary),
    )

    combined_for_tokens = "\n\n".join(filter(None, [image_request["image_prompt"], context_summary]))
    model = await provision_langchain_model(
        combined_for_tokens,
        planner_model_id,
        "chat",
        max_tokens=2048,
    )
    planner_messages = [
        SystemMessage(content=_planner_system_prompt()),
        HumanMessage(content=_human_planner_prompt(image_request["image_prompt"], context_summary)),
    ]

    planner_response = await model.ainvoke(planner_messages)
    plan_text = render_message_content(planner_response) or ""
    final_prompt = _extract_final_prompt(plan_text or image_request["image_prompt"])

    logger.debug(
        "Image planner produced plan_len={} final_prompt_len={}",
        len(plan_text),
        len(final_prompt),
    )

    try:
        mime_type, base64_data = await generate_nano_banana_image(
            prompt=final_prompt,
            model_name=image_request["image_model"]["name"],
        )
    except NanoBananaError as exc:
        logger.error(
            "Nano Banana generation failed model=%s prompt_len=%s err=%s",
            image_request["image_model"]["name"],
            len(final_prompt),
            exc,
        )
        raise

    logger.debug(
        "Image generated model={} mime={} bytes={}",
        image_request["image_model"]["name"],
        mime_type,
        len(base64_data),
    )

    data_url = f"data:{mime_type};base64,{base64_data}"
    provider = image_request["image_model"]["provider"]
    model_name = image_request["image_model"]["name"]

    message_parts = [
        f"### Image Plan ({model_name})",
        plan_text.strip(),
        f"![Generated with {model_name}]({data_url})",
        f"_Image model: {model_name} ({provider})_",
    ]
    content = "\n\n".join(part for part in message_parts if part)
    return AIMessage(content=content)
