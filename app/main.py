"""Дашборд радара: вход, таблица заказов, карточка, крутилки."""

from __future__ import annotations

import hmac
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import config, dadata, db, store
from app.filters import effective_filters, ui_hints
from app.present import MISSING, PROFILE_LABELS, STATUS_LABELS, grouped_keywords
from app.store import ALLOWED_STATUS

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
ICON_PNG = APP_DIR / "static" / "favicon-192.png"
FAVICON_ICO = APP_DIR / "static" / "favicon.ico"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    yield


app = FastAPI(
    title="Радар заказов",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.TRUSTED_HOSTS)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie=config.SESSION_COOKIE_NAME,
    max_age=config.SESSION_HOURS * 3600,
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
)


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon_ico() -> FileResponse:
    return FileResponse(FAVICON_ICO, media_type="image/x-icon")


@app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/apple-touch-icon-precomposed.png", methods=["GET", "HEAD"], include_in_schema=False)
async def apple_touch_icon() -> FileResponse:
    return FileResponse(ICON_PNG, media_type="image/png")


app.mount(
    "/static",
    StaticFiles(directory=str(APP_DIR / "static")),
    name="static",
)


def _is_authed(request: Request) -> bool:
    return request.session.get("user") == config.DASHBOARD_LOGIN


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _valid_csrf(request: Request, submitted: str) -> bool:
    expected = request.session.get("csrf", "")
    return expected != "" and hmac.compare_digest(expected, submitted or "")


def _deny() -> JSONResponse:
    return JSONResponse({"error": "нужен вход"}, status_code=401)


def _query_params(request: Request) -> dict[str, Any]:
    keys = (
        "q",
        "keyword",
        "source",
        "fz",
        "profile",
        "status",
        "has_phone",
        "hot",
        "date_from",
        "date_to",
        "region",
        "amount_min",
        "amount_max",
    )
    return {key: request.query_params.get(key, "") for key in keys}


def _cs_block(conn, inn: str) -> dict[str, Any]:
    if not inn or inn == MISSING:
        return {"text": "истории контрактов пока нет", "total": None}
    row = db.get_org(conn, inn, "clearspending")
    if row is None or not row["payload"]:
        return {"text": "истории контрактов пока нет", "total": None}
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return {"text": "истории контрактов пока нет", "total": None}
    total = payload.get("total")
    if total in (None, ""):
        return {"text": "истории контрактов пока нет", "total": None}
    return {
        "total": total,
        "text": f"В открытых данных у победителя ещё {total} контрактов",
        "sample": payload.get("sample") or [],
    }


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    if not _is_authed(request):
        return RedirectResponse(url="/login", status_code=303)
    filters = effective_filters()
    hints = ui_hints()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "login": config.DASHBOARD_LOGIN,
            "site_host": config.SITE_HOST,
            "csrf": _csrf_token(request),
            "hints": hints,
            "filters": filters,
            "status_labels": STATUS_LABELS,
            "profile_labels": PROFILE_LABELS,
            "keyword_groups_ui": grouped_keywords(filters["keywords"]),
            "boot": {
                "csrf": _csrf_token(request),
                "amountMin": filters["amount_min"],
                "dateFrom": filters.get("date_from_default") or "",
                "keywords": filters["keywords"],
                "regions": filters.get("regions") or [],
                "profiles": PROFILE_LABELS,
                "statuses": STATUS_LABELS,
                "hints": hints,
            },
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    if _is_authed(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "csrf": _csrf_token(request),
            "site_host": config.SITE_HOST,
            "hints": ui_hints(),
        },
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
) -> RedirectResponse:
    if not _valid_csrf(request, csrf):
        return RedirectResponse(url="/login?error=1", status_code=303)
    login_ok = hmac.compare_digest(username.strip(), config.DASHBOARD_LOGIN)
    password_ok = hmac.compare_digest(password, config.DASHBOARD_PASSWORD)
    if not (login_ok and password_ok):
        return RedirectResponse(url="/login?error=1", status_code=303)
    request.session.clear()
    request.session["user"] = config.DASHBOARD_LOGIN
    request.session["csrf"] = secrets.token_urlsafe(32)
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/api/settings")
async def api_settings(request: Request) -> JSONResponse:
    if not _is_authed(request):
        return _deny()
    data = effective_filters()
    data["hints"] = ui_hints()
    data["status_labels"] = STATUS_LABELS
    data["profile_labels"] = PROFILE_LABELS
    return JSONResponse(data)


@app.post("/api/settings")
async def api_settings_save(request: Request) -> JSONResponse:
    if not _is_authed(request):
        return _deny()
    payload = await request.json()
    csrf = str(payload.get("csrf") or request.headers.get("x-csrf") or "")
    if not _valid_csrf(request, csrf):
        return JSONResponse({"error": "сессия устарела, обновите страницу"}, status_code=403)
    conn = db.connect()
    try:
        if "amount_min" in payload:
            amount = int(payload["amount_min"])
            if amount < 0 or amount > 10_000_000_000:
                return JSONResponse({"error": "сумма вне разумных границ"}, status_code=400)
            db.upsert_setting(conn, "amount_min", str(amount))
        if "disabled_profiles" in payload:
            allowed = set(PROFILE_LABELS)
            disabled = [
                str(item)
                for item in (payload.get("disabled_profiles") or [])
                if str(item) in allowed
            ]
            db.upsert_setting(conn, "disabled_profiles", json.dumps(disabled, ensure_ascii=False))
        conn.commit()
        return JSONResponse(effective_filters(conn))
    finally:
        conn.close()


@app.get("/api/lots")
async def api_lots(request: Request) -> JSONResponse:
    if not _is_authed(request):
        return _deny()
    params = _query_params(request)
    conn = db.connect()
    try:
        items = store.list_lots(conn, params)
        return JSONResponse({"lots": items, "tiles": store.tiles(items)})
    finally:
        conn.close()


@app.get("/api/lots/{lot_id}")
async def api_lot(request: Request, lot_id: int) -> JSONResponse:
    if not _is_authed(request):
        return _deny()
    conn = db.connect()
    try:
        item = store.get_lot(conn, lot_id)
        if item is None:
            return JSONResponse({"error": "заказ не найден"}, status_code=404)
        winner_inn = item["winner_inn"]
        customer_inn = item["customer_inn"]
        winner_org = None
        customer_org = None
        if winner_inn != MISSING:
            winner_org = dadata.cached(conn, winner_inn) or dadata.fetch_and_store(conn, winner_inn)
        if customer_inn != MISSING:
            customer_org = dadata.cached(conn, customer_inn) or dadata.fetch_and_store(
                conn, customer_inn
            )
        item["winner_org"] = winner_org
        item["customer_org"] = customer_org
        item["clearspending"] = _cs_block(conn, winner_inn if winner_inn != MISSING else "")
        return JSONResponse(item)
    finally:
        conn.close()


@app.post("/api/lots/{lot_id}/status")
async def api_lot_status(request: Request, lot_id: int) -> JSONResponse:
    if not _is_authed(request):
        return _deny()
    payload = await request.json()
    csrf = str(payload.get("csrf") or request.headers.get("x-csrf") or "")
    if not _valid_csrf(request, csrf):
        return JSONResponse({"error": "сессия устарела, обновите страницу"}, status_code=403)
    status = str(payload.get("status") or "")
    if status not in ALLOWED_STATUS:
        return JSONResponse({"error": "неизвестный статус"}, status_code=400)
    conn = db.connect()
    try:
        ok = db.set_lot_status(conn, lot_id, status)
        if not ok:
            return JSONResponse({"error": "заказ не найден"}, status_code=404)
        db.add_event(conn, "status_changed", lot_id=lot_id, payload=status)
        conn.commit()
        item = store.get_lot(conn, lot_id)
        return JSONResponse(item or {"ok": True, "status": status})
    finally:
        conn.close()
