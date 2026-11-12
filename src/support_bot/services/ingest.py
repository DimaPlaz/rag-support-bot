import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db import repositories
from support_bot.db.models import Document
from support_bot.rag.chunking import chunk_text
from support_bot.rag.embeddings import Embedder
from support_bot.rag.loaders import LoadedDocument, is_url, iter_files


class IngestStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class IngestResult:
    source: str
    title: str
    status: IngestStatus
    chunk_count: int


class EmptyDocumentError(ValueError):
    pass


def collect_targets(target: str) -> list[str]:
    """Разворачивает аргумент CLI в список источников: URL, файл или все файлы папки."""
    if is_url(target):
        return [target]
    path = Path(target)
    if path.is_dir():
        return [str(file) for file in iter_files(path)]
    if path.is_file():
        return [target]
    raise FileNotFoundError(f"No such file or directory: {target}")


async def ingest_document(
    session: AsyncSession,
    embedder: Embedder,
    loaded: LoadedDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> IngestResult:
    """Сохраняет документ с чанками и эмбеддингами.

    Тот же текст (по хешу) — пропуск, даже под другим именем. Тот же источник с новым
    текстом — чанки заменяются. Иначе создаётся новый документ.
    """
    content_hash = hashlib.sha256(loaded.text.encode()).hexdigest()
    existing = await repositories.get_document_by_hash(session, content_hash)
    if existing is not None:
        return IngestResult(existing.source, existing.title, IngestStatus.SKIPPED, 0)

    chunks = chunk_text(loaded.text, chunk_size, chunk_overlap)
    if not chunks:
        raise EmptyDocumentError(f"No text found in {loaded.source}")
    embeddings = await embedder.embed_documents(chunks)

    document = await repositories.get_document_by_source(session, loaded.source)
    if document is None:
        document = Document(source=loaded.source, source_type=loaded.source_type)
        session.add(document)
        status = IngestStatus.CREATED
    else:
        status = IngestStatus.UPDATED
    document.title = loaded.title
    document.source_type = loaded.source_type
    document.content_hash = content_hash
    await session.flush()

    await repositories.replace_chunks(session, document, chunks, embeddings)
    await session.commit()
    return IngestResult(loaded.source, loaded.title, status, len(chunks))
