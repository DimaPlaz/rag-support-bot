import asyncio
import io
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from support_bot.db.models import SourceType

FILE_TYPES: dict[str, SourceType] = {
    ".md": SourceType.MARKDOWN,
    ".markdown": SourceType.MARKDOWN,
    ".txt": SourceType.TEXT,
    ".pdf": SourceType.PDF,
}

_HEADING_TAG = re.compile(r"^h[1-6]$")
_NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "form", "svg")


class UnsupportedSourceError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedDocument:
    source: str
    source_type: SourceType
    title: str
    text: str


def is_url(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def source_key(path: Path) -> str:
    """Стабильный ключ файла: путь относительно рабочей папки, одинаковый локально и в Docker."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return resolved.as_posix()


async def load_source(target: str, http_client: httpx.AsyncClient) -> LoadedDocument:
    if is_url(target):
        return await load_url(target, http_client)
    return await load_file(Path(target))


async def load_file(path: Path) -> LoadedDocument:
    source_type = FILE_TYPES.get(path.suffix.lower())
    if source_type is None:
        raise UnsupportedSourceError(f"Unsupported file type: {path.suffix or path.name}")

    if source_type is SourceType.PDF:
        title, text = await asyncio.to_thread(_read_pdf, path)
    else:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        title = _markdown_title(text) if source_type is SourceType.MARKDOWN else None

    return LoadedDocument(
        source=source_key(path),
        source_type=source_type,
        title=title or _title_from_filename(path),
        text=_normalize(text),
    )


async def load_url(url: str, http_client: httpx.AsyncClient) -> LoadedDocument:
    response = await http_client.get(url, follow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        raise UnsupportedSourceError(f"Expected an HTML page, got {content_type or 'unknown'}")

    title, text = _html_to_markdown(response.text)
    return LoadedDocument(
        source=url, source_type=SourceType.URL, title=title or url, text=_normalize(text)
    )


async def load_upload(filename: str, content: bytes) -> LoadedDocument:
    """Файл, загруженный через админку. Источник — upload/<имя файла>."""
    name = Path(filename).name or "document"
    source_type = FILE_TYPES.get(Path(name).suffix.lower())
    if source_type is None:
        raise UnsupportedSourceError(f"Unsupported file type: {Path(name).suffix or name}")

    if source_type is SourceType.PDF:
        title, text = await asyncio.to_thread(_read_pdf, io.BytesIO(content))
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise UnsupportedSourceError("Text files must be UTF-8 encoded") from error
        title = _markdown_title(text) if source_type is SourceType.MARKDOWN else None

    return LoadedDocument(
        source=f"upload/{name}",
        source_type=source_type,
        title=title or _title_from_filename(Path(name)),
        text=_normalize(text),
    )


def _read_pdf(source: Path | io.BytesIO) -> tuple[str | None, str]:
    reader = PdfReader(source)
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    title = reader.metadata.title if reader.metadata else None
    return title or _first_line(text), text


def _html_to_markdown(html: str) -> tuple[str | None, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    # Заголовки страницы превращаем в Markdown, чтобы чанкинг делил текст по разделам.
    for heading in root.find_all(_HEADING_TAG):
        level = int(heading.name[1])
        heading.replace_with(f"\n\n{'#' * level} {heading.get_text(' ', strip=True)}\n\n")
    return title, root.get_text("\n")


def _markdown_title(text: str) -> str | None:
    match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1) if match else None


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return None


def _title_from_filename(path: Path) -> str:
    return re.sub(r"[-_]+", " ", path.stem).strip().capitalize()


def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def iter_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in FILE_TYPES
        and not any(part.startswith(".") for part in path.relative_to(directory).parts)
    )
