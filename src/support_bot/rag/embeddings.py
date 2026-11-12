import asyncio
import threading
from functools import lru_cache
from typing import Protocol

from fastembed import TextEmbedding

from support_bot.config import get_settings
from support_bot.db.models import EMBEDDING_DIM


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """Локальные эмбеддинги через fastembed (ONNX, CPU).

    Модель загружается лениво при первом вызове: это скачивание ~1 ГБ, и API
    не должен ждать его на старте. Вычисления идут в потоке, чтобы не блокировать event loop.
    """

    def __init__(self, model_name: str, dim: int, cache_dir: str | None = None) -> None:
        self._model_name = model_name
        self._dim = dim
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_passages, texts)

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_query, text)

    def _embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._get_model().passage_embed(texts)]

    def _embed_query(self, text: str) -> list[float]:
        return next(iter(self._get_model().query_embed(text))).tolist()

    def _get_model(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                model = TextEmbedding(self._model_name, cache_dir=self._cache_dir)
                if model.embedding_size != self._dim:
                    raise ValueError(
                        f"Model {self._model_name} produces {model.embedding_size}-dim vectors, "
                        f"but the database expects {self._dim}"
                    )
                self._model = model
            return self._model


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    return FastEmbedEmbedder(settings.embedding_model, EMBEDDING_DIM, settings.embedding_cache_dir)
