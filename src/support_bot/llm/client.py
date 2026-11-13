from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import anthropic
import openai
from anthropic.types import MessageParam
from openai.types.chat import ChatCompletionMessageParam
from openai.types.completion_usage import CompletionUsage

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
        except Exception as error:
            # Не только APIError: без ключа SDK бросает TypeError ещё до запроса.
            # Любой сбой вызова должен заканчиваться handoff, а не потерянным вопросом.
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


# finish_reason OpenAI → stop_reason Anthropic, чтобы сервис ответа проверял одно и то же
_FINISH_REASONS = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class OpenAICompatLLMClient:
    """Claude через OpenAI-совместимый прокси (chat completions)."""

    def __init__(
        self, client: openai.AsyncOpenAI, *, model: str, effort: Effort, max_tokens: int
    ) -> None:
        self._client = client
        self._model = model
        self._effort: Effort = effort
        self._max_tokens = max_tokens

    async def stream(
        self, *, system: str, messages: list[MessageParam], schema: dict[str, Any]
    ) -> AsyncIterator[LLMEvent]:
        parts: list[str] = []
        finish_reason: str | None = None
        model = self._model
        usage: CompletionUsage | None = None
        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=_to_openai_messages(system, messages),
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "answer", "strict": True, "schema": schema},
                },
                # Прокси переводит reasoning_effort в effort Claude; xhigh/max в OpenAI нет.
                reasoning_effort="high" if self._effort in ("xhigh", "max") else self._effort,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                model = chunk.model or model
                usage = chunk.usage or usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    parts.append(choice.delta.content)
                    yield TextDelta(choice.delta.content)
                finish_reason = choice.finish_reason or finish_reason
        except Exception as error:
            raise LLMError(f"{type(error).__name__}: {error}") from error

        yield Completion(
            stop_reason=_FINISH_REASONS.get(finish_reason or "", finish_reason),
            text="".join(parts),
            model=model,
            usage=_openai_usage(usage),
        )


def _to_openai_messages(
    system: str, messages: list[MessageParam]
) -> list[ChatCompletionMessageParam]:
    result: list[ChatCompletionMessageParam] = [{"role": "system", "content": system}]
    for message in messages:
        content = message["content"]
        if not isinstance(content, str):
            raise TypeError("only text messages are supported")
        if message["role"] == "user":
            result.append({"role": "user", "content": content})
        else:
            result.append({"role": "assistant", "content": content})
    return result


def _openai_usage(usage: CompletionUsage | None) -> TokenUsage:
    if usage is None:
        return TokenUsage()
    details = usage.prompt_tokens_details
    cache_read = (details.cached_tokens if details else None) or 0
    # Запись в кеш прокси отдаёт нестандартным полем; prompt_tokens включает и кеш.
    extra = details.model_extra if details and details.model_extra else {}
    cache_write = int(extra.get("cache_creation_tokens") or 0)
    return TokenUsage(
        input_tokens=max(usage.prompt_tokens - cache_read - cache_write, 0),
        output_tokens=usage.completion_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    api_key = settings.anthropic_api_key
    # pydantic-settings читает .env, но не кладёт его в окружение, поэтому ключ передаём явно
    secret = api_key.get_secret_value() if api_key else None
    if settings.llm_provider == "openai":
        return OpenAICompatLLMClient(
            openai.AsyncOpenAI(
                # без ключа SDK падает при создании клиента; пусть упадёт запрос и будет handoff
                api_key=secret or "not-set",
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
            ),
            model=settings.llm_model,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
        )
    client = anthropic.AsyncAnthropic(
        api_key=secret, base_url=settings.llm_base_url, timeout=settings.llm_timeout_seconds
    )
    return AnthropicLLMClient(
        client,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
    )
