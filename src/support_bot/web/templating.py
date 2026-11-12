from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _usd(value: Decimal | None) -> str:
    if value is None:
        return "—"
    # доли цента важны: один ответ стоит порядка $0.01
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def _when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "—"


templates.env.filters["usd"] = _usd
templates.env.filters["when"] = _when
