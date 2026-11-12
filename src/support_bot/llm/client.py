from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from anthropic.types import MessageParam

from support_bot.config import Effort, get_settings
from support_bot.llm.pricing import TokenUsage


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Completion:
    stop_reason: str | None
    text: str
    model: str
    usage: TokenUsage


LLMEvent = TextDelta | Completion


class LLMError(Exception):
    """Вызов LLM не удался: сеть, лимиты, ошибка API."""


class LLMClient(Protocol):
    def stream(
        self, *, system: str, messages: list[MessageParam], schema: dict[str, Any]
    ) -> AsyncIterator[LLMEvent]:
        """Стримит текст ответа, последним событием — Completion."""
        ...


class AnthropicLLMClient:
    def __init__(
        self, client: anthropic.AsyncAnthropic, *, model: str, effort: Effort, max_tokens: int
    ) -> None:
        self._client = client
        self._model = model
        self._effort: Effort = effort
        self._max_tokens = max_tokens

    async def stream(
        self, *, system: str, messages: list[MessageParam], schema: dict[str, Any]
    ) -> AsyncIterator[LLMEvent]:
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=messages,
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield TextDelta(event.text)
                message = await stream.get_final_message()
        except anthropic.APIError as error:
            raise LLMError(f"{type(error).__name__}: {error}") from error

        usage = message.usage
        yield Completion(
            stop_reason=message.stop_reason,
            text="".join(block.text for block in message.content if block.type == "text"),
            model=message.model,
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    api_key = settings.anthropic_api_key
    client = anthropic.AsyncAnthropic(
        # pydantic-settings читает .env, но не кладёт его в окружение, поэтому ключ передаём явно
        api_key=api_key.get_secret_value() if api_key else None,
        timeout=settings.llm_timeout_seconds,
    )
    return AnthropicLLMClient(
        client,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
    )
