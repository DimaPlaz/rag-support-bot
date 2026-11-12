import json
import random
from typing import Any

import pytest

from support_bot.llm.json_stream import FieldDelta, FieldValue, JsonObjectStream, JsonStreamError

ANSWER = {
    "answer_found": True,
    "answer": 'Unit "M" costs $99.\nNo \\ problem — да 😀',
    "source_ids": [1, 3],
}


def _run(parts: list[str]) -> tuple[JsonObjectStream, str, dict[str, Any]]:
    parser = JsonObjectStream()
    streamed = ""
    values: dict[str, Any] = {}
    for part in parts:
        for event in parser.feed(part):
            if isinstance(event, FieldDelta) and event.name == "answer":
                streamed += event.text
            elif isinstance(event, FieldValue):
                values[event.name] = event.value
    return parser, streamed, values


def _split_randomly(text: str, rng: random.Random) -> list[str]:
    cuts = sorted(rng.sample(range(1, len(text)), k=rng.randint(1, min(20, len(text) - 1))))
    return [text[i:j] for i, j in zip([0, *cuts], [*cuts, len(text)], strict=True)]


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_random_splits_give_same_result(ensure_ascii: bool) -> None:
    text = json.dumps(ANSWER, ensure_ascii=ensure_ascii)
    rng = random.Random(42)
    for _ in range(200):
        parser, streamed, values = _run(_split_randomly(text, rng))
        assert parser.done
        assert values == ANSWER
        assert streamed == ANSWER["answer"]


def test_char_by_char_streams_answer_incrementally() -> None:
    parser = JsonObjectStream()
    deltas = [
        event.text
        for char in json.dumps(ANSWER, indent=2)
        for event in parser.feed(char)
        if isinstance(event, FieldDelta)
    ]
    assert len(deltas) > 10
    assert "".join(deltas) == ANSWER["answer"]


def test_surrogate_pair_split_between_chunks_is_not_broken() -> None:
    _, streamed, _ = _run(['{"answer": "hi \\ud83d', '\\ude00!"}'])
    assert streamed == "hi 😀!"


def test_answer_found_known_before_answer_text() -> None:
    parser = JsonObjectStream()
    events = parser.feed('{"answer_found": false, "answer": "')
    assert FieldValue("answer_found", False) in events


def test_field_order_does_not_matter() -> None:
    reordered = {"source_ids": [], "answer": "Text", "answer_found": True}
    _, streamed, values = _run([json.dumps(reordered)])
    assert values == reordered
    assert streamed == "Text"


def test_nested_values_with_tricky_strings() -> None:
    obj = {"meta": {"a": '}"]', "b": [1, {"c": None}]}, "answer": "ok", "n": 42}
    _, _, values = _run([json.dumps(obj)])
    assert values == obj


def test_invalid_json_raises() -> None:
    with pytest.raises(JsonStreamError):
        JsonObjectStream().feed('{"answer" "missing colon"}')
