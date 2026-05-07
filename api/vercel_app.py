"""
Vercel ASGI entry point for daily_stock_analysis.
Wraps the existing FastAPI app with Telegram Login support.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path

# ── Ensure project root is on sys.path ──────────────────────────
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Vercel environment overrides ────────────────────────────────
os.environ.setdefault("VERCEL", "1")

# Vercel 环境中用 /tmp 存储临时 SQLite 数据
os.environ.setdefault("DATABASE_PATH", "/tmp/stock_analysis.db")
os.environ.setdefault("LOG_DIR", "/tmp/logs")

# 禁用 Vercel 环境不支持的本地功能
os.environ.setdefault("SCHEDULE_ENABLED", "false")
os.environ.setdefault("BOT_ENABLED", "false")
os.environ.setdefault("ENABLE_CHIP_DISTRIBUTION", "false")
os.environ.setdefault("MD2IMG_ENGINE", "disabled")
os.environ.setdefault("MARKDOWN_TO_IMAGE_CHANNELS", "")

# tiktoken 缓存目录（Vercel 只读文件系统问题）
os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp/tiktoken_cache")

# ── Core app creation ───────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

# Import the existing API router (routes under /api/v1/*)
from api.v1 import api_v1_router

# ── Telegram Login Configuration ────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
COOKIE_NAME = "tg_session"
SESSION_EXPIRY_HOURS = 24 * 7  # 7 days

# ⚠️ In-memory session store — sessions lost on cold start.
# For production: replace with Redis, JWT, or Neon/Postgres.
_sessions: dict = {}


def _verify_telegram_auth(auth_data: dict) -> dict | None:
    """
    Verify Telegram Login Widget callback data using HMAC-SHA256.

    Args:
        auth_data: Dict with id, first_name, username, auth_date, hash, etc.

    Returns:
        Validated user dict if verification passes, None otherwise.
    """
    if not TELEGRAM_BOT_TOKEN:
        return None

    received_hash = auth_data.pop("hash", "")
    if not received_hash:
        return None

    # Build data-check string: sorted key=value lines
    items = sorted(auth_data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in items)

    # HMAC-SHA256: secretkey = SHA256(bot_token)
    secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # auth_date must be within 1 hour
    auth_date = int(auth_data.get("auth_date", 0))
    if time.time() - auth_date > 3600:
        return None

    return auth_data


def _create_session(user_data: dict) -> str:
    """Create a session token for an authenticated user."""
    session_id = secrets.token_hex(32)  # 128-bit entropy
    _sessions[session_id] = {
        "user": user_data,
        "created_at": time.time(),
    }
    return session_id


def _verify_session(session_id: str) -> dict | None:
    """Return user data if session is valid, None otherwise."""
    session = _sessions.get(session_id)
    if not session:
        return None
    if time.time() - session["created_at"] > SESSION_EXPIRY_HOURS * 3600:
        del _sessions[session_id]
        return None
    return session["user"]


def _delete_session(session_id: str) -> None:
    """Remove a session."""
    _sessions.pop(session_id, None)


# ── Build the Vercel-facing FastAPI app ────────────────────────
app = FastAPI(
    title="Daily Stock Analysis (Vercel)",
    description="A股智能分析系统 — Vercel deployment with Telegram Login",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(_project_root) / "static"
TELEGRAM_LOGIN_PAGE = Path(__file__).parent / "telegram_login.html"


# ────────────────────────────────────────────────────────────────
#  Routes
# ────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# --- Telegram Auth Endpoints -------------------------------------

@app.post("/api/auth/telegram/login")
async def telegram_login(request: Request):
    """
    Verify Telegram Login callback and create session.

    Expects JSON body from Telegram Login Widget onAuth callback.
    """
    if not TELEGRAM_BOT_TOKEN:
        return JSONResponse(
            status_code=400,
            content={"error": "not_configured", "message": "Telegram 登录未配置"},
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_body", "message": "请求格式无效"},
        )

    user = _verify_telegram_auth(body)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_auth", "message": "验证失败，请重新登录"},
        )

    session_id = _create_session(user)

    # Determine if this is a JSON API call or form redirect
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    wants_json = "application/json" in accept or "application/json" in content_type

    if wants_json:
        resp = JSONResponse(content={
            "ok": True,
            "user": {
                "id": user.get("id"),
                "username": user.get("username"),
                "firstName": user.get("first_name"),
            },
        })
    else:
        resp = JSONResponse(content={
            "ok": True,
            "redirect": "/app/",
        })
        resp.headers["HX-Redirect"] = "/app/"

    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=SESSION_EXPIRY_HOURS * 3600,
    )
    return resp


@app.get("/api/auth/telegram/status")
async def telegram_auth_status(request: Request):
    """Return current authentication status."""
    session_id = request.cookies.get(COOKIE_NAME)
    user = _verify_session(session_id) if session_id else None

    if user:
        return {
            "loggedIn": True,
            "user": {
                "id": user.get("id"),
                "username": user.get("username"),
                "firstName": user.get("first_name"),
            },
        }
    return {"loggedIn": False}


@app.post("/api/auth/telegram/logout")
async def telegram_logout(request: Request):
    """Clear session and cookie."""
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        _delete_session(session_id)

    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


# --- Frontend Routes ---------------------------------------------

@app.get("/", include_in_schema=False)
async def root(request: Request):
    """
    Root route — show Telegram login if configured, else main app.
    If already logged in, redirect to /app/.
    """
    # Check if user is already logged in
    session_id = request.cookies.get(COOKIE_NAME)
    user = _verify_session(session_id) if session_id else None
    if user:
        return RedirectResponse(url="/app/")

    # Show Telegram login page if configured
    if TELEGRAM_BOT_TOKEN and TELEGRAM_LOGIN_PAGE.exists():
        return FileResponse(TELEGRAM_LOGIN_PAGE)

    # Otherwise fall back to main SPA
    if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
        return FileResponse(STATIC_DIR / "index.html")

    return HTMLResponse("""
    <h1>Daily Stock Analysis API</h1>
    <p>Visit <a href="/api/health">/api/health</a></p>
    <p>API docs: <a href="/api/v1/docs">/api/v1/docs</a></p>
    """)


@app.get("/app/{full_path:path}", include_in_schema=False)
async def app_spa(request: Request, full_path: str = ""):
    """SPA pages — behind /app/ prefix."""
    # Check auth
    session_id = request.cookies.get(COOKIE_NAME)
    user = _verify_session(session_id) if session_id else None
    if not user:
        # Not logged in — redirect to root
        if request.headers.get("HX-Request") == "true":
            resp = JSONResponse(content={"redirect": "/"})
            resp.status_code = 401
            return resp
        return RedirectResponse(url="/")

    # Serve SPA index.html
    if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
        return FileResponse(STATIC_DIR / "index.html")
    return HTMLResponse("<h1>App (placeholder)</h1><p>Frontend not built.</p>")


@app.get("/assets/{file_path:path}", include_in_schema=False)
async def serve_assets(file_path: str):
    """Serve compiled frontend assets."""
    asset = STATIC_DIR / "assets" / file_path
    if asset.exists():
        # Explicit media type to avoid MIME sniffing issues
        import mimetypes
        content_type, _ = mimetypes.guess_type(str(asset))
        return FileResponse(asset, media_type=content_type)
    return JSONResponse(status_code=404, content={"error": "not_found"})


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    fav = STATIC_DIR / "vite.svg"
    if fav.exists():
        return FileResponse(fav)
    return JSONResponse(status_code=404, content={"error": "not_found"})


# ── Include existing API routes ──────────────────────────────────
# api_v1_router already has prefix=/api/v1 baked in.
# All existing endpoints (analysis, stocks, history, auth, backtest, etc.)
# are registered at /api/v1/* — same paths as the original app.
app.include_router(api_v1_router)


# ── Local development entry point ──────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("vercel_app:app", host="0.0.0.0", port=port, reload=True)
