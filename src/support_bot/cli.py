import argparse
import asyncio
import logging
import sys

import httpx

from support_bot.config import get_settings
from support_bot.db.session import engine, session_factory
from support_bot.rag.embeddings import get_embedder
from support_bot.rag.loaders import load_source
from support_bot.services.ingest import collect_targets, ingest_document

logger = logging.getLogger("kb")


async def ingest(targets: list[str]) -> int:
    settings = get_settings()
    embedder = get_embedder()
    failed = 0
    async with httpx.AsyncClient(timeout=30) as http_client:
        for target in targets:
            try:
                sources = collect_targets(target)
            except FileNotFoundError as error:
                logger.error("%s", error)
                failed += 1
                continue
            if not sources:
                logger.warning("No supported files in %s", target)

            for source in sources:
                try:
                    loaded = await load_source(source, http_client)
                    async with session_factory() as session:
                        result = await ingest_document(
                            session,
                            embedder,
                            loaded,
                            chunk_size=settings.chunk_size,
                            chunk_overlap=settings.chunk_overlap,
                        )
                except Exception:
                    logger.exception("Failed to ingest %s", source)
                    failed += 1
                    continue
                logger.info(
                    "%-8s %s — %s (%d chunks)",
                    result.status,
                    result.source,
                    result.title,
                    result.chunk_count,
                )
    await engine.dispose()
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="kb", description="BoxHub knowledge base tools")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser("ingest", help="Load documents into the knowledge base")
    ingest_parser.add_argument("targets", nargs="+", help="File, directory or page URL")
    args = parser.parse_args()

    logging.basicConfig(level=get_settings().log_level, format="%(message)s")
    # httpx пишет каждый запрос к Hugging Face при скачивании модели
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(ingest(args.targets)))
