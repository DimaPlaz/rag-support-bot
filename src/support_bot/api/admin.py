import logging
import math
import secrets
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.config import get_settings
from support_bot.db import repositories
from support_bot.db.session import get_session
from support_bot.rag.embeddings import Embedder, get_embedder
from support_bot.rag.loaders import LoadedDocument, is_url, load_upload, load_url
from support_bot.services.handoff import REASON_LABELS
from support_bot.services.ingest import IngestStatus, ingest_document
from support_bot.services.stats import collect_stats
from support_bot.web.templating import templates

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_basic = HTTPBasic(auto_error=False)


async def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> str:
    settings = get_settings()
    if settings.admin_password is None:
        # Пароль не задан — делаем вид, что админки нет.
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    expected = (settings.admin_username, settings.admin_password.get_secret_value())
    if credentials is None or not (
        # оба сравнения выполняются всегда, чтобы время ответа не выдавало, что угадано
        secrets.compare_digest(credentials.username.encode(), expected[0].encode())
        & secrets.compare_digest(credentials.password.encode(), expected[1].encode())
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="BoxHub admin"'},
        )
    return credentials.username


async def require_same_origin(request: Request) -> None:
    """Защита форм от CSRF: браузер сам шлёт basic auth в запросах с чужих сайтов."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin or urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request rejected")


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=30) as client:
        yield client


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], include_in_schema=False)

Session = Annotated[AsyncSession, Depends(get_session)]


def _render(request: Request, name: str, active: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(
        request, f"admin/{name}", {"active": active, "reasons": REASON_LABELS, **context}
    )


@router.get("", response_class=HTMLResponse)
async def overview(request: Request, session: Session) -> HTMLResponse:
    stats = await collect_stats(session)
    latest, _ = await repositories.list_messages(session, unanswered_only=True, limit=5, offset=0)
    return _render(request, "overview.html", "overview", stats=stats, latest=latest)


@router.get("/documents", response_class=HTMLResponse)
async def documents(
    request: Request,
    session: Session,
    notice: str | None = None,
    level: str = "info",
) -> HTMLResponse:
    rows = await repositories.list_documents(session)
    return _render(
        request,
        "documents.html",
        "documents",
        documents=rows,
        notice=notice,
        level=level,
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    )


@router.post("/documents", dependencies=[Depends(require_same_origin)])
async def upload_document(
    session: Session,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    file: Annotated[UploadFile | None, File()] = None,
    url: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        loaded = await _load(file, url.strip(), http_client)
        settings = get_settings()
        result = await ingest_document(
            session,
            embedder,
            loaded,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
    except ValueError as error:
        return _redirect_with_notice(str(error), "error")
    except httpx.HTTPError as error:
        return _redirect_with_notice(f"Could not fetch the page: {error}", "error")
    except Exception:
        logger.exception("Document upload failed")
        return _redirect_with_notice("Upload failed, see server logs.", "error")

    messages = {
        IngestStatus.CREATED: f"Added “{result.title}” ({result.chunk_count} chunks).",
        IngestStatus.UPDATED: f"Updated “{result.title}” ({result.chunk_count} chunks).",
        IngestStatus.SKIPPED: f"“{result.title}” is already in the knowledge base.",
    }
    return _redirect_with_notice(messages[result.status], "success")


async def _load(
    file: UploadFile | None, url: str, http_client: httpx.AsyncClient
) -> LoadedDocument:
    if file is not None and file.filename:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        return await load_upload(file.filename, content)
    if url:
        if not is_url(url):
            raise ValueError("URL must start with http:// or https://")
        return await load_url(url, http_client)
    raise ValueError("Choose a file or enter a page URL.")


def _redirect_with_notice(notice: str, level: str) -> RedirectResponse:
    # Post/Redirect/Get: обновление страницы не отправит форму повторно.
    query = urlencode({"notice": notice, "level": level})
    return RedirectResponse(f"/admin/documents?{query}", status.HTTP_303_SEE_OTHER)


async def _messages_page(
    request: Request, session: AsyncSession, *, unanswered_only: bool, page: int
) -> HTMLResponse:
    rows, total = await repositories.list_messages(
        session,
        unanswered_only=unanswered_only,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    active = "unanswered" if unanswered_only else "conversations"
    return _render(
        request,
        "messages.html",
        active,
        rows=rows,
        total=total,
        page=page,
        pages=max(1, math.ceil(total / PAGE_SIZE)),
        unanswered_only=unanswered_only,
    )


@router.get("/conversations", response_class=HTMLResponse)
async def conversations(
    request: Request, session: Session, page: Annotated[int, Query(ge=1)] = 1
) -> HTMLResponse:
    return await _messages_page(request, session, unanswered_only=False, page=page)


@router.get("/unanswered", response_class=HTMLResponse)
async def unanswered(
    request: Request, session: Session, page: Annotated[int, Query(ge=1)] = 1
) -> HTMLResponse:
    return await _messages_page(request, session, unanswered_only=True, page=page)


@router.get("/conversations/{conversation_id}", response_class=HTMLResponse)
async def conversation(request: Request, session: Session, conversation_id: int) -> HTMLResponse:
    found = await repositories.get_conversation(session, conversation_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    dialog, messages = found
    return _render(
        request, "conversation.html", "conversations", conversation=dialog, messages=messages
    )
