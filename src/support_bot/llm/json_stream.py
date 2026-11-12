import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

_WHITESPACE = " \t\r\n"


class JsonStreamError(ValueError):
    pass


@dataclass(frozen=True)
class FieldDelta:
    """Очередной кусок строкового поля верхнего уровня (уже без JSON-экранирования)."""

    name: str
    text: str


@dataclass(frozen=True)
class FieldValue:
    """Поле верхнего уровня получено целиком."""

    name: str
    value: Any


JsonEvent = FieldDelta | FieldValue


class _State(Enum):
    BEFORE_OBJECT = auto()
    EXPECT_KEY = auto()
    IN_KEY = auto()
    EXPECT_COLON = auto()
    EXPECT_VALUE = auto()
    IN_STRING = auto()
    IN_OTHER = auto()
    AFTER_VALUE = auto()
    DONE = auto()


class JsonObjectStream:
    """Потоковый разбор JSON-объекта, который модель генерирует по кускам.

    Нужен, чтобы показывать пользователю поле `answer` по мере генерации,
    не дожидаясь конца ответа. Строковые поля верхнего уровня отдаются кусками
    (FieldDelta) и целиком (FieldValue), остальные значения — только целиком.
    """

    def __init__(self) -> None:
        self._state = _State.BEFORE_OBJECT
        self._key = ""
        self._raw = ""  # сырые символы текущего ключа или значения
        self._escaped = False  # предыдущий символ строки — обратный слеш
        self._emitted = 0  # сколько символов строкового значения уже отдано
        self._depth = 0
        self._in_nested_string = False

    @property
    def done(self) -> bool:
        return self._state is _State.DONE

    def feed(self, chunk: str) -> list[JsonEvent]:
        events: list[JsonEvent] = []
        for char in chunk:
            self._feed_char(char, events)
        if self._state is _State.IN_STRING:
            self._emit_string_delta(self._raw[: _safe_prefix_len(self._raw)], events)
        return events

    def _feed_char(self, char: str, events: list[JsonEvent]) -> None:
        state = self._state
        if state is _State.IN_STRING:
            self._feed_string(char, events)
        elif state is _State.IN_KEY:
            self._feed_key(char)
        elif state is _State.IN_OTHER:
            self._feed_other(char, events)
        elif char in _WHITESPACE:
            return
        elif state is _State.BEFORE_OBJECT and char == "{":
            self._state = _State.EXPECT_KEY
        elif state is _State.EXPECT_KEY and char == '"':
            self._raw = ""
            self._state = _State.IN_KEY
        elif state in (_State.EXPECT_KEY, _State.AFTER_VALUE) and char == "}":
            self._state = _State.DONE
        elif state is _State.AFTER_VALUE and char == ",":
            self._state = _State.EXPECT_KEY
        elif state is _State.EXPECT_COLON and char == ":":
            self._state = _State.EXPECT_VALUE
        elif state is _State.EXPECT_VALUE:
            self._start_value(char)
        else:
            raise JsonStreamError(f"Unexpected {char!r} in state {state.name}")

    def _is_string_end(self, char: str) -> bool:
        if self._escaped:
            self._escaped = False
            return False
        if char == "\\":
            self._escaped = True
            return False
        return char == '"'

    def _feed_key(self, char: str) -> None:
        if self._is_string_end(char):
            self._key = _loads_string(self._raw)
            self._state = _State.EXPECT_COLON
        else:
            self._raw += char

    def _feed_string(self, char: str, events: list[JsonEvent]) -> None:
        if not self._is_string_end(char):
            self._raw += char
            return
        value = self._emit_string_delta(self._raw, events)
        events.append(FieldValue(self._key, value))
        self._state = _State.AFTER_VALUE

    def _emit_string_delta(self, raw: str, events: list[JsonEvent]) -> str:
        decoded = _loads_string(raw)
        if len(decoded) > self._emitted:
            events.append(FieldDelta(self._key, decoded[self._emitted :]))
            self._emitted = len(decoded)
        return decoded

    def _start_value(self, char: str) -> None:
        self._escaped = False
        if char == '"':
            self._raw = ""
            self._emitted = 0
            self._state = _State.IN_STRING
            return
        self._raw = char
        self._depth = 1 if char in "[{" else 0
        self._in_nested_string = False
        self._state = _State.IN_OTHER

    def _feed_other(self, char: str, events: list[JsonEvent]) -> None:
        if self._in_nested_string:
            self._raw += char
            if self._is_string_end(char):
                self._in_nested_string = False
            return

        if self._depth == 0 and char in ",}":
            # скаляр (true, 42, null) заканчивается на разделителе
            events.append(FieldValue(self._key, _loads_value(self._raw)))
            self._state = _State.EXPECT_KEY if char == "," else _State.DONE
            return

        self._raw += char
        if char == '"':
            self._in_nested_string = True
        elif char in "[{":
            self._depth += 1
        elif char in "]}":
            self._depth -= 1
            if self._depth == 0:
                events.append(FieldValue(self._key, _loads_value(self._raw)))
                self._state = _State.AFTER_VALUE


def _safe_prefix_len(raw: str) -> int:
    """Длина префикса сырой строки, который можно раскодировать прямо сейчас.

    Префикс не обрывается посреди escape-последовательности и не отделяет
    старший суррогат (\\uD83D) от младшего — иначе эмодзи развалится на два символа.
    """
    i, n = 0, len(raw)
    while i < n:
        if raw[i] != "\\":
            i += 1
            continue
        if i + 1 >= n:
            return i
        if raw[i + 1] != "u":
            i += 2
            continue
        if i + 6 > n:
            return i
        if _is_high_surrogate(raw[i + 2 : i + 6]):
            following = raw[i + 6 : i + 8]
            if following == "\\u":
                if i + 12 > n:
                    return i
                i += 12
                continue
            if len(following) < 2 and "\\u".startswith(following):
                return i  # младший суррогат может прийти в следующем куске
        i += 6
    return n


def _is_high_surrogate(hex_digits: str) -> bool:
    try:
        return 0xD800 <= int(hex_digits, 16) <= 0xDBFF
    except ValueError:
        return False


def _loads_string(body: str) -> str:
    try:
        return json.loads(f'"{body}"')
    except json.JSONDecodeError as error:
        raise JsonStreamError(f"Invalid JSON string: {body!r}") from error


def _loads_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise JsonStreamError(f"Invalid JSON value: {raw!r}") from error
