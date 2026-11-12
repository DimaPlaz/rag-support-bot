from dataclasses import dataclass
from decimal import Decimal

_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPrice:
    """Цены в $ за 1M токенов."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


# Тарифы Claude API на 2026-10. Запись в кеш (TTL 5 минут) стоит 1.25 × input.
PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(Decimal("4"), Decimal("20"), Decimal("0.20"), Decimal("5")),
    "claude-sonnet-5-5": ModelPrice(Decimal("2"), Decimal("10"), Decimal("0.20"), Decimal("2.5")),
    # Цена чтения кеша для Haiku 5.5 не уточнена — берём input, чтобы не занизить стоимость.
    "claude-haiku-5-5": ModelPrice(
        Decimal("0.10"), Decimal("0.50"), Decimal("0.10"), Decimal("0.125")
    ),
}


def calculate_cost(model: str, usage: TokenUsage) -> Decimal | None:
    """Стоимость вызова в $; None, если цена модели неизвестна."""
    price = PRICES.get(model)
    if price is None:
        return None
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return (total / _PER_MILLION).quantize(Decimal("0.000001"))
