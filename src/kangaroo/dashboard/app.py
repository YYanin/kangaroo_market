"""Local read-only dashboard — FastAPI + Jinja2.

Binds to 127.0.0.1 only per AGENTS.md hardware-context rule.
All timestamps stored in UTC; displayed in America/New_York.
No UI element suggests placing or sizing a trade.
The only write action available is closing a ladder (status → user_closed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from kangaroo.config import get_settings
from kangaroo.db import repository as repo

# The host this app should be bound to.  Used by the __main__ block and checked
# by the regression-prevention test.
DASHBOARD_HOST: str = "127.0.0.1"

_ET = ZoneInfo("America/New_York")
_TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Kangaroo Dashboard")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_db_path() -> str:
    """FastAPI dependency — returns the configured DB path.  Override in tests."""
    return get_settings().db_path


def _et_label(ts_utc: str | None) -> str:
    """Convert an ISO-8601 UTC string to a human-readable ET string."""
    if not ts_utc:
        return ""
    try:
        dt = datetime.fromisoformat(ts_utc).replace(tzinfo=UTC).astimezone(_ET)
        return dt.strftime("%Y-%m-%d %H:%M ET")
    except ValueError:
        return ts_utc


# ---------------------------------------------------------------------------
# Today page
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def today(
    request: Request,
    db_path: str = Depends(get_db_path),
) -> HTMLResponse:
    date_prefix = datetime.now(UTC).strftime("%Y-%m-%d")
    alerts = await repo.get_today_alerts(db_path, date_prefix=date_prefix)

    enriched = []
    for alert in alerts:
        evidence = await repo.get_evidence_for_alert(db_path, int(alert["id"]))
        enriched.append(
            {
                "alert": alert,
                "evidence": evidence,
                "ts_et": _et_label(alert.get("timestamp_utc")),
            }
        )

    return templates.TemplateResponse(
        request, "today.html", {"alerts": enriched, "date": date_prefix}
    )


# ---------------------------------------------------------------------------
# Ladders page
# ---------------------------------------------------------------------------


@app.get("/ladders", response_class=HTMLResponse)
async def ladders(
    request: Request,
    db_path: str = Depends(get_db_path),
) -> HTMLResponse:
    settings = get_settings()
    step_pct = settings.ladder.step_pct
    active = await repo.get_active_tracked_tickers(db_path)

    ladder_cards = []
    for row in active:
        last_price = float(row["last_alert_price"])
        next_trigger = round(last_price * (1 - step_pct / 100.0), 2)
        ladder_cards.append(
            {
                "row": row,
                "next_trigger": next_trigger,
                "first_ts_et": _et_label(row.get("first_alert_timestamp_utc")),
            }
        )

    return templates.TemplateResponse(request, "ladders.html", {"ladders": ladder_cards})


# ---------------------------------------------------------------------------
# Close ladder endpoint (the only write action on the dashboard)
# ---------------------------------------------------------------------------


@app.post("/ladders/{ticker}/close")
async def close_ladder(
    ticker: str,
    db_path: str = Depends(get_db_path),
) -> RedirectResponse:
    await repo.close_tracked_ticker(
        db_path,
        ticker=ticker,
        status="user_closed",
        closed_timestamp_utc=datetime.now(UTC).isoformat(),
        closed_reason="user_closed",
    )
    return RedirectResponse(url="/ladders", status_code=303)


# ---------------------------------------------------------------------------
# Performance page
# ---------------------------------------------------------------------------


@app.get("/performance", response_class=HTMLResponse)
async def performance(
    request: Request,
    db_path: str = Depends(get_db_path),
) -> HTMLResponse:
    stats = await repo.get_performance_stats(db_path)
    return templates.TemplateResponse(request, "performance.html", {"stats": stats})


# ---------------------------------------------------------------------------
# Entry point — for manual `python -m kangaroo.dashboard.app`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("kangaroo.dashboard.app:app", host=DASHBOARD_HOST, port=8000, reload=False)
