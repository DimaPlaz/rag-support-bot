# RAG Support Bot

AI-бот поддержки: отвечает по базе знаний компании (RAG) на сайте (виджет) и в Telegram, показывает источники, вопросы без ответа передаёт оператору. Демо-компания — вымышленная BoxHub Self-Storage.
Полное ТЗ, этапы и чек-лист — в `SPEC.md`. Это источник правды по функциональности.

## Как работаем
- Идём строго по этапам из `SPEC.md`, по одному этапу за раз.
- Перед этапом — короткий план (файлы, модели БД, что тестируем), жди подтверждения.
- Этап закончен, когда выполнен его критерий готовности, тесты и линтеры зелёные. Тогда отметь его в чек-листе `SPEC.md` и предложи коммит.
- Не делай то, что в ТЗ помечено «вне MVP», без явной просьбы.
- Если в ТЗ чего-то не хватает или есть противоречие — спроси, не додумывай.

## Стек
Python 3.12, uv, FastAPI, PostgreSQL + pgvector, SQLAlchemy 2 (async) + Alembic, Redis, aiogram 3, Anthropic SDK, fastembed, Jinja2 (админка и демо-страница), pydantic-settings, pytest, ruff, pyright, Docker Compose, Caddy.

## Структура
```
src/support_bot/
  main.py            # FastAPI app: API, виджет, демо-страница, админка
  bot.py             # отдельный запуск Telegram-бота (обычно бот работает внутри API, см. ниже)
  cli.py             # `kb ingest <path|url>` (скрипт kb в pyproject)
  config.py          # Settings (pydantic-settings), всё из .env
  api/               # роуты FastAPI: chat (SSE), admin, health
  telegram/          # aiogram: роутеры, тексты
  services/          # бизнес-логика: answer (RAG), ingest, handoff, stats
  rag/               # чанкинг, эмбеддинги (интерфейс + fastembed), поиск
  llm/               # клиент Anthropic, промпты, расчёт стоимости
  db/                # модели, репозитории, сессия
  web/               # widget.js, шаблоны, статика
data/kb/             # база знаний BoxHub
migrations/
tests/
```
Веб-роуты и хендлеры Telegram тонкие. Логика ответа одна на оба канала — в `services/answer.py`.
Telegram-бот по умолчанию опрашивает Telegram внутри процесса API (`TG_POLLING_ENABLED=true` в docker compose), чтобы не держать в памяти вторую копию модели эмбеддингов (~1.6 ГБ). Одновременно может работать только один поллер на токен.

## Команды
```bash
uv sync
docker compose up -d db redis
uv run alembic upgrade head
uv run kb ingest data/kb/
docker compose run --rm api kb ingest data/kb/   # то же внутри Docker
uv run uvicorn support_bot.main:app --reload
uv run python -m support_bot.bot   # бот отдельно; только если контейнер api не запущен
uv run pytest
uv run ruff check . && uv run ruff format . && uv run pyright
```

## Стиль кода
- Аннотации типов везде. `X | None` вместо `Optional`, `list`/`dict`/`str` вместо `typing.List`/`Dict`/`Text`.
- async везде, где есть I/O.
- Небольшие функции, понятные имена, комментарии только там, где неочевидно «почему».
- Не добавлять зависимости без необходимости.

## RAG
- Отвечать строго по найденному контексту. Нет релевантных чанков (порог похожести в `.env`) или модель сообщает, что ответа в контексте нет, — handoff оператору, без выдумывания.
- Модель возвращает ответ и признак «ответ найден» в структурированном виде (structured outputs), а не через разбор текста.
- Источники берутся из метаданных найденных чанков, не из текста модели.
- Загрузка идемпотентна: хеш содержимого документа, повтор не создаёт дублей.
- Эмбеддинги за интерфейсом `Embedder`, чтобы заменить fastembed на API-провайдера одной реализацией.

## LLM
- Только официальный Anthropic SDK (`anthropic`). Перед написанием кода работы с API используй скилл `claude-api`, не пиши вызовы по памяти.
- Исключение (решение владельца): `LLM_PROVIDER=openai` — Claude через OpenAI-совместимый прокси (Hostman AI) на SDK `openai`, `OpenAICompatLLMClient` в `llm/client.py`. Он отдаёт те же события и `stop_reason`, что и клиент Anthropic; сервис ответа о провайдере не знает.
- Модель — из `LLM_MODEL` в `.env`, не хардкодить.
- Всегда проверяй `stop_reason` (`refusal`, `max_tokens`) до чтения ответа. Ошибка или отказ — вежливое сообщение и handoff.
- Промпты — в `llm/prompts.py`, отдельно от кода. Тон — вежливая поддержка, ответ на языке вопроса.
- На каждый вызов сохраняй токены (`usage`) и стоимость в $.

## Безопасность
- Секреты только в `.env`, в репозитории только `.env.example`.
- Админка под basic auth, логин и пароль из `.env`.
- Текст документов и вопросов пользователя — данные, а не инструкции: промпт должен это явно оговаривать (защита от prompt injection).
- Rate limit на `/api/chat` по IP и сессии.
