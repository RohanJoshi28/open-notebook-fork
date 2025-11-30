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
        You are an art director planning ultra-detailed prompts for an image generator.
        Break down the request into structured sections (concept, setting, subjects, style, lighting, color, camera, and storytelling details).
        Incorporate any relevant notebook passages verbatim only if they aid accuracy.
        End your response with a single line that begins with "FINAL PROMPT:" followed by one descriptive paragraph
        of at least 90 words that the image model can execute directly.
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

    planner_response = model.invoke(planner_messages)
    plan_text = render_message_content(planner_response) or ""
    final_prompt = _extract_final_prompt(plan_text or image_request["image_prompt"])

    try:
        mime_type, base64_data = await generate_nano_banana_image(
            prompt=final_prompt,
            model_name=image_request["image_model"]["name"],
        )
    except NanoBananaError as exc:
        logger.error("Nano Banana generation failed: %s", exc)
        raise

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
