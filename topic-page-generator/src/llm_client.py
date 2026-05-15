from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .cache import get_cache

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"


class LLMClient:
    """Thin Anthropic SDK wrapper with structured output (tool-use) and caching."""

    def __init__(self) -> None:
        # anthropic.Anthropic() reads ANTHROPIC_API_KEY from env automatically
        self._client = anthropic.Anthropic()
        self._cache = get_cache()

    def call_structured(
        self,
        messages: list[dict],
        model: str,
        schema_class: type[T],
        tool_description: str = "Output the structured result.",
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> T | None:
        """Call the API with forced tool-use structured output.

        Returns a validated Pydantic model instance.
        On validation failure: one retry with error fed back via tool_result.
        On second failure: logs and returns None (degrade gracefully).
        """
        cache_key = _make_cache_key(messages, model, schema_class.__name__, system)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("LLM cache hit  %s / %s", model, schema_class.__name__)
            return schema_class.model_validate(cached)

        tool_def = {
            "name": "output",
            "description": tool_description,
            "input_schema": schema_class.model_json_schema(),
        }
        base_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "tools": [tool_def],
            "tool_choice": {"type": "tool", "name": "output"},
        }
        if system:
            base_kwargs["system"] = system

        result = self._call_with_retry(base_kwargs, list(messages), schema_class)
        if result is not None:
            self._cache.set(cache_key, result.model_dump())
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _call_with_retry(
        self,
        base_kwargs: dict,
        messages: list[dict],
        schema_class: type[T],
    ) -> T | None:
        for attempt in range(2):
            try:
                response = self._client.messages.create(
                    **base_kwargs, messages=messages
                )
            except Exception as exc:
                logger.error("Anthropic API error (attempt %d): %s", attempt + 1, exc)
                return None

            tool_block = next(
                (b for b in response.content if b.type == "tool_use"), None
            )
            if tool_block is None:
                logger.warning(
                    "No tool_use block in response (attempt %d, model %s)",
                    attempt + 1,
                    base_kwargs["model"],
                )
                return None

            try:
                return schema_class.model_validate(tool_block.input)
            except ValidationError as exc:
                if attempt == 0:
                    logger.warning(
                        "Validation error on attempt 1 (%s), retrying with error feedback:\n%s",
                        schema_class.__name__,
                        exc,
                    )
                    # Extend the conversation: assistant turn + tool_result error
                    messages = messages + [
                        {"role": "assistant", "content": response.content},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_block.id,
                                    "content": (
                                        f"Your output failed schema validation:\n{exc}\n"
                                        "Fix all errors and call the tool again."
                                    ),
                                    "is_error": True,
                                }
                            ],
                        },
                    ]
                else:
                    logger.error(
                        "Validation error on attempt 2 (%s), degrading gracefully:\n%s",
                        schema_class.__name__,
                        exc,
                    )
                    return None

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache_key(
    messages: list,
    model: str,
    schema_name: str,
    system: str | None,
) -> str:
    payload = {
        "messages": messages,
        "model": model,
        "schema": schema_name,
        "system": system,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return "llm:" + hashlib.sha256(raw.encode()).hexdigest()[:32]
