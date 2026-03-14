"""Nova LLM Service — unified wrapper for Amazon Nova Pro/Lite via OpenAI-compatible API.

Handles all text/multimodal AI calls through LiteLLM or OpenRouter gateway.
Used by: listing_agent, conversation_agent, negotiation_agent, scheduling.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
from openai import AsyncOpenAI

from project.config import get_config

logger = logging.getLogger(__name__)


class NovaLLMService:
    """Unified service for calling Amazon Nova Pro and Nova Lite models."""

    def __init__(self):
        self._config = get_config()
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._config.nova.openai_api_key,
                base_url=self._config.nova.openai_base_url,
                timeout=httpx.Timeout(120.0),
            )
        return self._client

    async def call(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> str:
        """Call a Nova model via the OpenAI-compatible API.

        Args:
            model: Model identifier (e.g., 'amazon/nova-pro-v1:0')
            messages: Chat messages in OpenAI format
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            max_retries: Number of retry attempts

        Returns:
            The model's text response, stripped of whitespace.
        """
        base_url = self._config.nova.openai_base_url
        logger.info(f"Calling {model} via {base_url}")

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                content = (
                    response.choices[0].message.content
                    if response.choices
                    else None
                )
                if content:
                    return content.strip()

                logger.warning(
                    f"Empty response from {model} (attempt {attempt}/{max_retries})"
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return ""

            except Exception as e:
                logger.error(
                    f"Error calling {model} (attempt {attempt}/{max_retries}): {e}"
                )
                if attempt == max_retries:
                    raise
                await asyncio.sleep(2 ** attempt)

        return ""

    async def call_nova_pro(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Call Amazon Nova Pro (reasoning model)."""
        return await self.call(
            model=self._config.nova.nova_pro_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def call_nova_lite(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        """Call Amazon Nova Lite (multimodal model)."""
        return await self.call(
            model=self._config.nova.nova_lite_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def call_with_image(
        self,
        system_prompt: str,
        text_prompt: str,
        image_base64: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """Call a Nova model with an image input (multimodal).

        Args:
            system_prompt: System message
            text_prompt: User text prompt
            image_base64: Base64-encoded image data
            model: Model to use (defaults to Nova Lite)
            temperature: Sampling temperature

        Returns:
            Model response text.
        """
        mime_type = detect_image_mime(image_base64)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}",
                        },
                    },
                    {"type": "text", "text": text_prompt},
                ],
            },
        ]

        return await self.call(
            model=model or self._config.nova.nova_lite_model,
            messages=messages,
            temperature=temperature,
            max_tokens=2048,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────────────────────


def parse_json_response(raw: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, handling markdown code blocks.

    Args:
        raw: Raw LLM response text

    Returns:
        Parsed JSON dictionary

    Raises:
        json.JSONDecodeError: If no valid JSON found
    """
    cleaned = raw.strip()

    # Remove markdown code blocks
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # Try to find JSON object in the response
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)

    return json.loads(cleaned)


def detect_image_mime(image_base64: str) -> str:
    """Detect MIME type from base64-encoded image data using magic bytes.

    Args:
        image_base64: Base64-encoded image data

    Returns:
        MIME type string (defaults to 'image/jpeg')
    """
    try:
        header = base64.b64decode(image_base64[:32])
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if header[:2] == b"\xff\xd8":
            return "image/jpeg"
        if header[:4] == b"GIF8":
            return "image/gif"
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
    except Exception:
        pass
    return "image/jpeg"


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_service: Optional[NovaLLMService] = None


def get_nova_llm() -> NovaLLMService:
    """Get or create the global Nova LLM service."""
    global _service
    if _service is None:
        _service = NovaLLMService()
    return _service