from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db.models import Channel, Chunk, Conversation, Document, Message, MessageStatus


@dataclass(frozen=True)
class Stats:
    total: int
    unanswered: int
    by_channel: dict[Channel, int]
    total_cost: Decimal
    average_cost: Decimal | None
    input_tokens: int
    output_tokens: int
    documents: int
    chunks: int

    @property
    def answered(self) -> int:
        return self.total - self.unanswered

    @property
    def unanswered_percent(self) -> float:
        return round(self.unanswered * 100 / self.total, 1) if self.total else 0.0


async def collect_stats(session: AsyncSession) -> Stats:
    totals = (
        await session.execute(
            select(
                func.count(Message.id),
                func.count(Message.id).filter(Message.status == MessageStatus.UNANSWERED),
                func.coalesce(func.sum(Message.cost_usd), 0),
                func.avg(Message.cost_usd),  # только вызовы LLM: у handoff без LLM cost = NULL
                func.coalesce(func.sum(Message.input_tokens), 0),
                func.coalesce(func.sum(Message.output_tokens), 0),
            )
        )
    ).one()
    channels = await session.execute(
        select(Conversation.channel, func.count(Message.id))
        .join(Message, Message.conversation_id == Conversation.id)
        .group_by(Conversation.channel)
    )
    documents = await session.scalar(select(func.count(Document.id))) or 0
    chunks = await session.scalar(select(func.count(Chunk.id))) or 0

    total, unanswered, total_cost, average_cost, input_tokens, output_tokens = totals
    by_channel = {channel: 0 for channel in Channel}
    by_channel.update({channel: count for channel, count in channels})
    return Stats(
        total=total,
        unanswered=unanswered,
        by_channel=by_channel,
        total_cost=Decimal(total_cost or 0),
        average_cost=_round_cost(average_cost),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        documents=documents,
        chunks=chunks,
    )


def _round_cost(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(Decimal("0.000001"))
