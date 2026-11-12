import logging
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from support_bot.db.models import Channel, HandoffReason
from support_bot.services.answer import AnswerDelta, AnswerService, get_answer_service
from support_bot.services.handoff import handoff_text
from support_bot.services.rate_limit import RateLimiter, get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")


async def enforce_rate_limit(
    payload: ChatRequest,
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    # Зависимость выполняется до начала стрима, поэтому 429 уходит обычным ответом.
    ip = request.client.host if request.client else "unknown"
    if not await limiter.allow(ip=ip, session_id=payload.session_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, please try again in a minute."
        )


@router.post(
    "/api/chat",
    response_class=EventSourceResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
async def chat(
    payload: ChatRequest, service: Annotated[AnswerService, Depends(get_answer_service)]
) -> AsyncIterable[ServerSentEvent]:
    try:
        async for event in service.answer(
            session_id=payload.session_id, channel=Channel.WEB, question=payload.question
        ):
            if isinstance(event, AnswerDelta):
                yield ServerSentEvent(event="delta", data={"text": event.text})
            else:
                yield ServerSentEvent(
                    event="done",
                    data={
                        "answered": event.answered,
                        "answer": event.answer,
                        "sources": [{"title": s.title, "url": s.url} for s in event.sources],
                        "message_id": event.message_id,
                    },
                )
    except Exception:
        logger.exception("Chat request failed")
        message = handoff_text(payload.question, HandoffReason.LLM_ERROR)
        yield ServerSentEvent(event="error", data={"message": message})
