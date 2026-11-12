from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from support_bot.web.templating import STATIC_DIR, templates

router = APIRouter()

# Цены Northgate из data/kb/pricing.md — витрина демо-страницы.
UNITS = [
    {"size": "XS", "area": "1 m²", "fits": "Boxes, suitcases, seasonal items", "price": 25},
    {"size": "S", "area": "3 m²", "fits": "A studio room, a bicycle, sports gear", "price": 49},
    {"size": "M", "area": "6 m²", "fits": "A one-bedroom apartment", "price": 85},
    {"size": "L", "area": "10 m²", "fits": "A two-bedroom apartment", "price": 129},
    {
        "size": "XL",
        "area": "15 m²",
        "fits": "A three-bedroom house or business stock",
        "price": 175,
    },
]

QUESTIONS = [
    "Which unit do I need for a one-bedroom apartment?",
    "Can I get into my unit at night?",
    "What happens if I pay 10 days late?",
    "Можно ли хранить бензин?",
    "Do you deliver pizza?",
]


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"units": UNITS, "questions": QUESTIONS}
    )


@router.get("/widget.js", name="widget", include_in_schema=False)
async def widget() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "widget.js",
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )
