from pathlib import Path

import httpx
import pytest

from support_bot.db.models import SourceType
from support_bot.rag.loaders import (
    UnsupportedSourceError,
    iter_files,
    load_file,
    load_source,
    load_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


async def test_markdown_title_from_first_heading(tmp_path: Path) -> None:
    path = tmp_path / "faq.md"
    path.write_text("Intro line\n\n# Real Title\n\nBody\n\n\n\nMore", encoding="utf-8")

    loaded = await load_file(path)

    assert loaded.source_type is SourceType.MARKDOWN
    assert loaded.title == "Real Title"
    assert loaded.text == "Intro line\n\n# Real Title\n\nBody\n\nMore"


async def test_text_file_title_from_filename(tmp_path: Path) -> None:
    path = tmp_path / "late-fees_policy.txt"
    path.write_text("Some text", encoding="utf-8")

    loaded = await load_file(path)

    assert loaded.source_type is SourceType.TEXT
    assert loaded.title == "Late fees policy"


async def test_pdf_text_and_title() -> None:
    loaded = await load_file(FIXTURES / "sample.pdf")

    assert loaded.source_type is SourceType.PDF
    assert loaded.title == "Sample PDF"
    assert "Hello from a PDF fixture." in loaded.text


async def test_unsupported_file_type(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG")

    with pytest.raises(UnsupportedSourceError):
        await load_file(path)


def test_iter_files_skips_hidden_and_unsupported(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_text("b")
    (tmp_path / "c.png").write_bytes(b"")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "d.md").write_text("d")

    names = [path.name for path in iter_files(tmp_path)]

    assert names == ["a.md", "b.txt"]


HTML_PAGE = """
<html><head><title>BoxHub Moving Tips</title><style>.x{}</style></head>
<body>
  <nav>Home | Pricing</nav>
  <main>
    <h1>Moving tips</h1>
    <p>Pack heavy items in small boxes.</p>
    <h2>Labels</h2>
    <p>Label every box.</p>
    <script>alert(1)</script>
  </main>
  <footer>Copyright</footer>
</body></html>
"""


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_url_converted_to_markdown_without_noise() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, html=HTML_PAGE),
    )
    async with _client(transport) as client:
        loaded = await load_source("https://boxhub.example/tips", client)

    assert loaded.source == "https://boxhub.example/tips"
    assert loaded.source_type is SourceType.URL
    assert loaded.title == "BoxHub Moving Tips"
    assert loaded.text == (
        "# Moving tips\n\nPack heavy items in small boxes.\n\n## Labels\n\nLabel every box."
    )


async def test_url_rejects_non_html() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"a": 1}))
    async with _client(transport) as client:
        with pytest.raises(UnsupportedSourceError):
            await load_url("https://boxhub.example/api", client)


async def test_url_http_error() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(404))
    async with _client(transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await load_url("https://boxhub.example/missing", client)
