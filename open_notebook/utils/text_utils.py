"""
Text utilities for Open Notebook.
Extracted from main utils to avoid circular imports.
"""

import re
import unicodedata
from typing import Any, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .token_utils import token_count

# Pattern for matching thinking content in AI responses
THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def split_text(txt: str, chunk_size=500):
    """
    Split the input text into chunks.

    Args:
        txt (str): The input text to be split.
        chunk_size (int): The size of each chunk. Default is 500.

    Returns:
        list: A list of text chunks.
    """
    overlap = int(chunk_size * 0.15)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        length_function=token_count,
        separators=[
            "\n\n",
            "\n",
            ".",
            ",",
            " ",
            "\u200b",  # Zero-width space
            "\uff0c",  # Fullwidth comma
            "\u3001",  # Ideographic comma
            "\uff0e",  # Fullwidth full stop
            "\u3002",  # Ideographic full stop
            "",
        ],
    )
    return text_splitter.split_text(txt)


def remove_non_ascii(text: str) -> str:
    """Remove non-ASCII characters from text."""
    return re.sub(r"[^\x00-\x7F]+", "", text)


def remove_non_printable(text: str) -> str:
    """Remove non-printable characters from text."""
    # Replace any special Unicode whitespace characters with a regular space
    text = re.sub(r"[\u2000-\u200B\u202F\u205F\u3000]", " ", text)

    # Replace unusual line terminators with a single newline
    text = re.sub(r"[\u2028\u2029\r]", "\n", text)

    # Remove control characters, except newlines and tabs
    text = "".join(
        char for char in text if unicodedata.category(char)[0] != "C" or char in "\n\t"
    )

    # Replace non-breaking spaces with regular spaces
    text = text.replace("\xa0", " ").strip()

    # Keep letters (including accented ones), numbers, spaces, newlines, tabs, and basic punctuation
    return re.sub(r"[^\w\s.,!?\-\n\t]", "", text, flags=re.UNICODE)


def parse_thinking_content(content: str) -> Tuple[str, str]:
    """
    Parse message content to extract thinking content from <think> tags.

    Args:
        content (str): The original message content

    Returns:
        Tuple[str, str]: (thinking_content, cleaned_content)
            - thinking_content: Content from within <think> tags
            - cleaned_content: Original content with <think> blocks removed

    Example:
        >>> content = "<think>Let me analyze this</think>Here's my answer"
        >>> thinking, cleaned = parse_thinking_content(content)
        >>> print(thinking)
        "Let me analyze this"
        >>> print(cleaned)
        "Here's my answer"
    """
    # Input validation
    if not isinstance(content, str):
        return "", str(content) if content is not None else ""

    # Limit processing for very large content (100KB limit)
    if len(content) > 100000:
        return "", content

    # Find all thinking blocks
    thinking_matches = THINK_PATTERN.findall(content)

    if not thinking_matches:
        return "", content

    # Join all thinking content with double newlines
    thinking_content = "\n\n".join(match.strip() for match in thinking_matches)

    # Remove all <think>...</think> blocks from the original content
    cleaned_content = THINK_PATTERN.sub("", content)

    # Clean up extra whitespace
    cleaned_content = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned_content).strip()

    return thinking_content, cleaned_content


def clean_thinking_content(content: str) -> str:
    """
    Remove thinking content from AI responses, returning only the cleaned content.

    This is a convenience function for cases where you only need the cleaned
    content and don't need access to the thinking process.

    Args:
        content (str): The original message content with potential <think> tags

    Returns:
        str: Content with <think> blocks removed and whitespace cleaned

    Example:
        >>> content = "<think>Let me think...</think>Here's the answer"
        >>> clean_thinking_content(content)
        "Here's the answer"
    """
    _, cleaned_content = parse_thinking_content(content)
    return cleaned_content


def render_message_content(content: Any) -> str:
    """
    Convert structured LangChain/Gemini message content into a plain string.

    Gemini 3 responses often come back as lists of typed content parts instead of simple
    strings. This helper normalizes those payloads so downstream code that expects text
    keeps working regardless of provider.
    """

    def _render_part(part: Any) -> str:
        if part is None:
            return ""
        if isinstance(part, str):
            return part
        if isinstance(part, list):
            return "".join(_render_part(p) for p in part)
        if isinstance(part, dict):
            # Prefer explicit text fields first
            if isinstance(part.get("text"), str):
                return part["text"]
            content_value = part.get("content")
            if isinstance(content_value, str):
                return content_value
            if content_value is not None:
                return _render_part(content_value)
            if "parts" in part:
                return _render_part(part["parts"])
            inline_data = part.get("inline_data")
            if isinstance(inline_data, dict):
                for key in ("text", "data"):
                    value = inline_data.get(key)
                    if isinstance(value, str):
                        return value
            # Fall back to any stringifiable value
            for key in ("argument", "data", "body"):
                if key in part and isinstance(part[key], str):
                    return part[key]
            return str(part)
        # Handle LangChain message chunk objects that expose .text or .content
        text_attr = getattr(part, "text", None)
        if isinstance(text_attr, str):
            return text_attr
        content_attr = getattr(part, "content", None)
        if isinstance(content_attr, str):
            return content_attr
        if content_attr is not None:
            return _render_part(content_attr)
        # Last resort
        return str(part)

    rendered = _render_part(content)
    return rendered.strip()
