from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://support_bot:support_bot@localhost:5432/support_bot"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    embedding_cache_dir: str | None = None
    chunk_size: int = 1000
    chunk_overlap: int = 150


@lru_cache
def get_settings() -> Settings:
    return Settings()
