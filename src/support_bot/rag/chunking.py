import re
import textwrap
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_SEP = "\n\n"
_SENTENCE_SEP = " "


@dataclass(frozen=True)
class Section:
    headings: tuple[str, ...]
    body: str


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Делит текст на чанки по разделам Markdown, затем по абзацам и предложениям.

    Цепочка заголовков раздела добавляется в начало каждого чанка: без неё кусок
    «Prepay 6 months: 10% off» теряет смысл при поиске.
    """
    chunks: list[str] = []
    for section in split_sections(text):
        prefix = " > ".join(section.headings)
        budget = max(chunk_size - len(prefix) - len(_PARAGRAPH_SEP), chunk_size // 2)
        for piece in _split_body(section.body, budget, chunk_overlap):
            chunks.append(f"{prefix}{_PARAGRAPH_SEP}{piece}" if prefix else piece)
    return chunks


def split_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append(Section(tuple(title for _, title in stack), content))
        body.clear()

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        level = len(match.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, match.group(2)))
    flush()
    return sections


def _split_body(body: str, budget: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    paragraphs: list[str] = []
    for paragraph in (p.strip() for p in body.split(_PARAGRAPH_SEP)):
        if not paragraph:
            continue
        if len(paragraph) <= budget:
            paragraphs.append(paragraph)
            continue
        chunks.extend(_pack(paragraphs, budget, overlap, _PARAGRAPH_SEP))
        paragraphs = []
        chunks.extend(_pack(_split_long(paragraph, budget), budget, overlap, _SENTENCE_SEP))
    chunks.extend(_pack(paragraphs, budget, overlap, _PARAGRAPH_SEP))
    return chunks


def _split_long(paragraph: str, budget: int) -> list[str]:
    units: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        if len(sentence) <= budget:
            units.append(sentence)
        else:
            units.extend(textwrap.wrap(sentence, budget))
    return units


def _pack(units: list[str], budget: int, overlap: int, sep: str) -> list[str]:
    """Жадно собирает куски до budget; новый чанк начинается с хвоста предыдущего (overlap)."""
    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and _joined_len([*current, unit], sep) > budget:
            chunks.append(sep.join(current))
            current = _tail(current, overlap, sep)
            if _joined_len([*current, unit], sep) > budget:
                current = []
        current.append(unit)
    if current:
        chunks.append(sep.join(current))
    return chunks


def _tail(units: list[str], overlap: int, sep: str) -> list[str]:
    tail: list[str] = []
    for unit in reversed(units):
        if _joined_len([unit, *tail], sep) > overlap:
            break
        tail.insert(0, unit)
    return tail


def _joined_len(units: list[str], sep: str) -> int:
    return sum(len(unit) for unit in units) + len(sep) * (len(units) - 1)
