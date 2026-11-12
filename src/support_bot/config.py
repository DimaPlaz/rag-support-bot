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
    rag_min_similarity: float = 0.30
    history_messages: int = 6

    # Домены сайтов, где подключён виджет (JSON-список), например ["https://boxhub.example"]
    cors_origins: list[str] = []

    tg_bot_token: SecretStr | None = None
    tg_operator_chat_id: int | None = None
    # Polling внутри процесса API. Включать только в одном запущенном процессе:
    # два поллера с одним токеном получают от Telegram ошибку 409.
    tg_polling_enabled: bool = False
    public_base_url: str = "http://localhost:8000"

    admin_username: str = "admin"
    # Без пароля админка выключена (404): пароля по умолчанию нет намеренно.
    admin_password: SecretStr | None = None

    rate_limit_ip_per_minute: int = 20
    rate_limit_session_per_minute: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
