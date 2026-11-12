from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://support_bot:support_bot@localhost:5432/support_bot"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    embedding_cache_dir: str | None = None
    chunk_size: int = 1000
    chunk_overlap: int = 150

    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: Effort = "low"
    llm_max_tokens: int = 8000
    llm_timeout_seconds: float = 60.0

    rag_top_k: int = 5
    rag_min_similarity: float = 0.35
    history_messages: int = 6

    rate_limit_ip_per_minute: int = 20
    rate_limit_session_per_minute: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
