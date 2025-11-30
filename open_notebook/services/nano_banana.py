import os
from typing import Optional, Tuple

import httpx
from loguru import logger


class NanoBananaError(RuntimeError):
    """Raised when the Nano Banana (Gemini image) API fails."""


async def generate_nano_banana_image(
    prompt: str,
    model_name: str,
    mime_type: str = "image/png",
    api_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Call Google's Generative Language API (Nano Banana family) to create an image.

    Returns:
        A tuple of (mime_type, base64_data).
    """

    key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise NanoBananaError(
            "GOOGLE_API_KEY (or GEMINI_API_KEY) must be set to use Nano Banana image models"
        )

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    )
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt,
                    }
                ],
            }
        ],
        "generationConfig": {
            "response_mime_type": mime_type,
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DEROGATORY", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_VIOLENCE", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUAL", "threshold": "BLOCK_ONLY_HIGH"},
        ],
        "responseModalities": ["IMAGE"],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(endpoint, params={"key": key}, json=payload)

    if response.status_code != 200:
        logger.error(
            "Nano Banana API error %s: %s",
            response.status_code,
            response.text,
        )
        raise NanoBananaError(
            f"Image generation failed with status {response.status_code}: {response.text}"
        )

    data = response.json()

    try:
        inline_data = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        mime = inline_data.get("mimeType", mime_type)
        base64_data = inline_data["data"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected Nano Banana API response: %s", data)
        raise NanoBananaError("Nano Banana API response did not include image data") from exc

    return mime, base64_data
