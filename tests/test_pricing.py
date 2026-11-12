from decimal import Decimal

from support_bot.llm.pricing import TokenUsage, calculate_cost


def test_cost_for_opus() -> None:
    usage = TokenUsage(input_tokens=2000, output_tokens=500, cache_read_tokens=1000)

    # 2000 × $4 + 500 × $20 + 1000 × $0.20 за 1M токенов
    assert calculate_cost("claude-opus-5-5", usage) == Decimal("0.018200")


def test_unknown_model_has_no_cost() -> None:
    assert calculate_cost("some-unknown-model", TokenUsage(input_tokens=10)) is None
