"""Quick one-shot vision describe used by passive group recording.

When a non-addressed group message includes a photo, we record the photo
to history for later context. The bot only "sees" history as text on
subsequent turns (we don't re-embed historical images as image_url blocks
— that scales catastrophically with chatty groups), so the path alone is
not enough to give the bot a sense of what was sent.

This helper does ONE small vision call at record time to produce a short
description that gets folded into the message content. Cheap and bounded.
Falls back to ``None`` if anything goes wrong; the caller should then keep
the plain ``[image]`` placeholder.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider


# A tight prompt — we want a single useful sentence, not a paragraph.
# Anchored around "chat context" so the model gives us a description that's
# actually helpful for a bot rejoining the conversation later (e.g. "PR review
# screenshot" beats "an image with text on a dark background").
_DESCRIBE_PROMPT = (
    "Describe this image in ONE short sentence (≤ 25 words) optimized for chat "
    "context — what would a person tell a friend who didn't see it? Focus on the "
    "subject and any visible text/UI. No preamble, no quotes, no commentary."
)

# Maximum bytes we'll send. Anything larger gets skipped — the cost / latency
# tradeoff isn't worth it for a passive recording.
_MAX_BYTES = 5 * 1024 * 1024


async def describe_image_for_history(
    path: str | Path,
    provider: "LLMProvider",
    model: str,
) -> str | None:
    """Return a short LLM-generated description of the image at *path*.

    Returns ``None`` on any failure (file missing, not an image, provider
    error, empty response). The caller is responsible for falling back to a
    placeholder when ``None`` is returned.
    """
    p = Path(path)
    try:
        if not p.is_file():
            return None
        raw = p.read_bytes()
        if len(raw) > _MAX_BYTES:
            logger.debug(
                "passive vision: skipping {} ({} bytes > {} cap)",
                p, len(raw), _MAX_BYTES,
            )
            return None
        mime = detect_image_mime(raw) or mimetypes.guess_type(str(p))[0]
        if not mime or not mime.startswith("image/"):
            return None
        b64 = base64.b64encode(raw).decode()
    except OSError as exc:
        logger.warning("passive vision: read failed for {}: {}", p, exc)
        return None

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
                {"type": "text", "text": _DESCRIBE_PROMPT},
            ],
        },
    ]
    try:
        response = await provider.chat_with_retry(
            messages=messages,
            model=model,
            max_tokens=120,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("passive vision: provider call failed for {}: {}", p, exc)
        return None

    text = (response.content or "").strip() if response else ""
    if not text:
        return None
    # Single-line, no surrounding quotes.
    text = text.replace("\n", " ").strip().strip('"').strip("'")
    # Hard cap so a model that ignores the prompt doesn't blow up history.
    if len(text) > 240:
        text = text[:237].rstrip() + "…"
    return text
