from itertools import pairwise

from support_bot.rag.chunking import chunk_text, split_sections


def test_split_sections_tracks_heading_path() -> None:
    text = "# Doc\nIntro\n## A\nText A\n### A1\nText A1\n## B\nText B"

    sections = split_sections(text)

    assert [(s.headings, s.body) for s in sections] == [
        (("Doc",), "Intro"),
        (("Doc", "A"), "Text A"),
        (("Doc", "A", "A1"), "Text A1"),
        (("Doc", "B"), "Text B"),
    ]


def test_heading_without_body_is_skipped() -> None:
    assert [s.headings for s in split_sections("# Doc\n## Empty\n## Full\nText")] == [
        ("Doc", "Full")
    ]


def test_chunk_prefixed_with_headings() -> None:
    chunks = chunk_text("# Pricing\n## Discounts\nPrepay 6 months: 10% off.", 500, 50)

    assert chunks == ["Pricing > Discounts\n\nPrepay 6 months: 10% off."]


def test_plain_text_without_headings() -> None:
    assert chunk_text("Just a line.\n\nAnother one.", 500, 50) == ["Just a line.\n\nAnother one."]


def test_empty_text_gives_no_chunks() -> None:
    assert chunk_text("", 500, 50) == []
    assert chunk_text("# Only heading\n\n", 500, 50) == []


def test_paragraphs_packed_within_size() -> None:
    paragraphs = [f"Paragraph {i} " + "x" * 80 for i in range(10)]

    chunks = chunk_text("\n\n".join(paragraphs), 300, 0)

    assert len(chunks) > 1
    assert all(len(chunk) <= 300 for chunk in chunks)
    # абзацы не режутся пополам
    assert all(chunk.count("Paragraph") == chunk.count("x" * 80) for chunk in chunks)


def test_overlap_repeats_tail_of_previous_chunk() -> None:
    paragraphs = [f"P{i} " + "y" * 60 for i in range(6)]

    chunks = chunk_text("\n\n".join(paragraphs), 200, 70)

    assert len(chunks) > 1
    for previous, current in pairwise(chunks):
        last_paragraph = previous.split("\n\n")[-1]
        assert current.startswith(last_paragraph)


def test_long_paragraph_split_by_sentences() -> None:
    paragraph = " ".join(f"Sentence number {i} is here." for i in range(50))

    chunks = chunk_text(paragraph, 200, 40)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks)


def test_long_word_is_hard_wrapped() -> None:
    chunks = chunk_text("z" * 450, 200, 0)

    assert all(len(chunk) <= 200 for chunk in chunks)
    assert "".join(chunks) == "z" * 450


def test_chunk_size_includes_heading_prefix() -> None:
    text = "# Long heading here\n" + "\n\n".join("w" * 90 for _ in range(10))

    assert all(len(chunk) <= 250 for chunk in chunk_text(text, 250, 0))
