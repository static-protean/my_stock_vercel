# Vercel 部署 + Telegram 登录集成指南

> **适用项目**: daily_stock_analysis（A股自选股智能分析系统）
> **最后更新**: 2026-05-08

---

## 目录

1. [整体架构说明](#1-整体架构说明)
2. [前置准备](#2-前置准备)
3. [创建 Telegram Bot](#3-创建-telegram-bot)
4. [步骤一：创建 Vercel 配置文件](#4-步骤一创建-verceljson)
5. [步骤二：创建 Vercel ASGI 入口](#5-步骤二创建-vercel-asgi-入口)
6. [步骤三：添加 Telegram Login 后端端点](#6-步骤三添加-telegram-login-后端端点)
7. [步骤四：创建 Telegram 登录页](#7-步骤四创建-telegram-登录页)
8. [步骤五：创建精简依赖文件](#8-步骤五创建精简依赖文件)
9. [步骤六：配置环境变量](#9-步骤六配置环境变量)
10. [步骤七：部署到 Vercel](#10-步骤七部署到-vercel)
11. [步骤八：配置 Bot 域名白名单](#11-步骤八配置-bot-域名白名单)
12. [用户使用流程](#12-用户使用流程)
13. [常见问题](#13-常见问题)

---

## 1. 整体架构说明

### 部署架构图

```
用户浏览器
    │
    ▼
Vercel CDN（全球加速）
    │
    ├─ / → Telegram 登录页（telegram_login.html）
    ├─ /app/* → 原有 SPA 前端（static/ 目录）
    └─ /api/* → Python Serverless Function（FastAPI）
                     │
                     ├─ 股票分析 API
                     ├─ 登录状态校验
                     └─ Telegram 登录回调
```

### 注意事项（Vercel 限制）

| 限制项 | 说明 | 解决方案 |
|--------|------|---------|
| **SQLite** | Vercel 文件系统为只读/临时，SQLite 无法持久化 | 推荐改用 PostgreSQL（可用 [Neon](https://neon.tech) 或 [Supabase](https://supabase.com) 免费版） |
| **API 超时** | Hobby 计划限 10 秒，Pro 计划限 60 秒 | 分析类 API 建议设为异步任务 |
| **冷启动** | 长时间无请求后首次加载较慢 | 可配置 Cron Job 定期唤醒 |
| **依赖体积** | 部署包不能超过 50MB | 需精简依赖（见步骤五） |

---

## 2. 前置准备

- [ ] **Vercel 账号** — 前往 [vercel.com](https://vercel.com) 用 GitHub 登录
- [ ] **GitHub 仓库** — 将项目推送到 GitHub
- [ ] **Telegram 账号** — 用于创建 Bot
- [ ] **Python 3.11+** 本地开发环境
- [ ] **Vercel CLI**（可选）：`npm install -g vercel`

---

## 3. 创建 Telegram Bot

通过 @BotFather 创建 Bot，获取 Bot Token，并配置域名白名单。

### 3.1 创建 Bot

在 Telegram 中搜索 **@BotFather**，发送：

```
/newbot
```

按提示依次输入：
1. Bot 名称（如 `股票智能分析`）
2. Bot 用户名（如 `MyStockBot`，必须以 `bot` 结尾）

创建成功后，@BotFather 会返回 **Bot Token**，形如：

```
1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

**务必保存好这个 Token**。

### 3.2 配置域名白名单（部署后执行）

部署完成后，将你的 Vercel 域名配置到 Bot：

```
/setdomain
```

选择刚创建的 Bot，输入你的域名，如 `your-app.vercel.app`。

> ⚠️ Telegram Login Widget **必须**设置域名才能工作。开发期间可以用 `localhost`，上线后换成正式域名。

---

## 4. 步骤一：创建 vercel.json

在项目根目录（与 `server.py` 同级）创建 `vercel.json`：

```json
{
  "functions": {
    "api/vercel_app.py": {
      "includeFiles": "**/*",
      "maxDuration": 30
    }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/vercel_app.py" },
    { "source": "/app/(.*)", "destination": "/api/vercel_app.py" },
    { "source": "/assets/(.*)", "destination": "/api/vercel_app.py" },
    { "source": "/", "destination": "/api/vercel_app.py" }
  ]
}
```

> **说明**：
> - 所有请求统一路由到 `api/vercel_app.py`
> - Vercel 会在 Serverless Function 中运行你的 FastAPI 应用
> - `maxDuration: 30` 将超时提升到 30 秒（Hobby 计划上限 10 秒，此处仅为 Pro 示例）

### 4.1 如何获取 Pro 计划的 30s 超时？

Hobby 计划默认所有函数 **10 秒超时**。你的 LLM 分析很可能超时。解决方案：
- 升 **Pro 计划**（$20/月）→ 可设置最长 300 秒
- 或优化 API，将耗时操作转为异步任务（返回 `task_id` 让前端轮询）

---

## 5. 步骤二：创建 Vercel ASGI 入口

创建 `api/vercel_app.py`。这是 Vercel Serverless Function 的入口，它导入并包装了现有的 FastAPI 应用，加入 Telegram 登录功能。

> ⚠️ **重要**：你的项目根目录下已有一个 `api/` 文件夹（Python 包）。Vercel 将 `api/*.py` 识别为 Serverless 函数端点，但我们的 `api/vercel_app.py` 作为总入口处理所有路由，其余文件作为普通模块导入。Vercel **不会**将 `api/` 子目录中的 `.py` 文件暴露为独立端点。

```python
"""
Vercel ASGI entry point for daily_stock_analysis.
Wraps the existing FastAPI app with Telegram Login support.
"""
import logging
import os
import sys
from pathlib import Path

# ── Ensure project root is on sys.path ──────────────────────────
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Lazy-import the real app ────────────────────────────────────
# (After configuring sys.path so imports resolve correctly)
os.environ.setdefault("VERCEL", "1")
os.environ.setdefault("ENV_FILE", os.path.join(_project_root, ".env"))

# ⚠️ Vercel 环境无 wkhtmltopdf，禁用图片转换相关功能
os.environ["MD2IMG_ENGINE"] = "disabled"
os.environ["ENABLE_CHIP_DISTRIBUTION"] = "false"
os.environ["SCHEDULE_ENABLED"] = "false"
os.environ["BOT_ENABLED"] = "false"

from src.config import setup_env
setup_env()

# Patch telemetry to avoid tiktoken crash in serverless env
os.environ["TIKTOKEN_CACHE_DIR"] = "/tmp/tiktoken_cache"

# ── FastAPI + Telegram auth ─────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.app import create_app as _create_fastapi_app, app as _existing_app

# ── Telegram auth helpers ───────────────────────────────────────
import hashlib
import hmac
import json
import time
import secrets

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
COOKIE_NAME = "telegram_session"
SESSION_EXPIRY_HOURS = 24 * 7  # 7 days

# Simple in-memory session store (Vercel 无持久化文件系统)
# ⚠️ 多实例 / 重启后 session 会丢失
# 生产环境建议改用 Redis 或 JWT
_sessions: dict = {}


def _verify_telegram_auth(auth_data: dict) -> dict | None:
    """
    Verify Telegram Login Widget callback data.
    Returns the validated user dict if valid, None otherwise.
    """
    if not TELEGRAM_BOT_TOKEN:
        return None

    received_hash = auth_data.pop("hash", "")
    if not received_hash:
        return None

    # Build data check string (sorted alphabetically)
    items = sorted(auth_data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in items)

    # Compute HMAC-SHA256
    secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if computed_hash != received_hash:
        return None

    # Check auth_date is within 1 hour
    auth_date = int(auth_data.get("auth_date", 0))
    if time.time() - auth_date > 3600:
        return None

    return auth_data


def _create_session(user_data: dict) -> str:
    """Create a session token for an authenticated user."""
    session_id = secrets.token_hex(32)
    _sessions[session_id] = {
        "user": user_data,
        "created_at": time.time(),
    }
    return session_id


def _verify_session(session_id: str) -> dict | None:
    """Check if a session token is valid and return user data."""
    session = _sessions.get(session_id)
    if not session:
        return None
    if time.time() - session["created_at"] > SESSION_EXPIRY_HOURS * 3600:
        del _sessions[session_id]
        return None
    return session["user"]


# ── Wrap the existing app ───────────────────────────────────────
app = FastAPI(
    title="Daily Stock Analysis (Vercel)",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ──────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/auth/telegram/login")
async def telegram_login(request: Request):
    """
    Receive Telegram Login Widget callback.
    Body: JSON with id, first_name, last_name, username, photo_url,
          auth_date, hash (signed by Telegram)
    """
    body = await request.json()
    user = _verify_telegram_auth(body)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_auth", "message": "验证失败，请重新登录"},
        )

    session_id = _create_session(user)
    resp = JSONResponse(content={"ok": True, "user": user})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=SESSION_EXPIRY_HOURS * 3600,
    )
    # Also redirect to main app
    resp.headers["HX-Redirect"] = "/app/"
    return resp


@app.get("/api/auth/telegram/status")
async def telegram_auth_status(request: Request):
    """Return current login status."""
    session_id = request.cookies.get(COOKIE_NAME)
    user = _verify_session(session_id) if session_id else None
    if user:
        return {"loggedIn": True, "user": {"id": user.get("id"), "username": user.get("username"), "firstName": user.get("first_name")}}
    return {"loggedIn": False}


@app.post("/api/auth/telegram/logout")
async def telegram_logout(request: Request):
    """Logout and clear session."""
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id and session_id in _sessions:
        del _sessions[session_id]
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


# ── Static file serving + SPA fallback ──────────────────────────

STATIC_DIR = Path(_project_root) / "static"
TELEGRAM_LOGIN_HTML = Path(__file__).parent / "telegram_login.html"
FRONTEND_INDEX = STATIC_DIR / "index.html"


@app.get("/", include_in_schema=False)
async def root():
    """Root → Telegram login page (if TELEGRAM_BOT_TOKEN is set) or main app."""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_LOGIN_HTML.exists():
        return FileResponse(TELEGRAM_LOGIN_HTML)
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return HTMLResponse("<h1>Daily Stock Analysis API</h1><p>Visit <a href='/docs'>/docs</a></p>")


@app.get("/app/{full_path:path}", include_in_schema=False)
async def app_spa(request: Request, full_path: str = ""):
    """SPA pages behind /app/"""
    # Check auth
    # session_id = request.cookies.get(COOKIE_NAME)
    # user = _verify_session(session_id) if session_id else None
    # if not user:
    #     return RedirectResponse(url="/")

    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return HTMLResponse("<h1>404</h1>")


@app.get("/assets/{file_path:path}", include_in_schema=False)
async def serve_assets(file_path: str):
    """Serve compiled frontend assets."""
    asset = STATIC_DIR / "assets" / file_path
    if asset.exists():
        return FileResponse(asset)
    return JSONResponse(status_code=404, content={"error": "not_found"})


# ── Mount existing FastAPI app routes (analysis API) ────────────
# We mount the existing api app under /api/v1
app.mount("/api/v1", _existing_app)


# ── For local testing ──────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("vercel_app:app", host="0.0.0.0", port=8000, reload=True)
```

### 5.1 关于 API 路由的说明

上述代码中，现有 FastAPI 应用通过 `app.mount()` 挂载在 `/api/v1` 下。但 Vercel 的 `rewrites` 规则会将 `/api/v1/*` 请求先路由到 `vercel_app.py`，再由内部 mount 处理。

**或用另一种方案**：你也可以不 mount，而是在此文件中重新导出需要的路由。但 mount 方式改动最小。

---

## 6. 步骤三：添加 Telegram Login 后端端点

上面的 `api/vercel_app.py` 已经包含了 Telegram 登录所需的所有端点。关键函数 `_verify_telegram_auth()` 做了以下事情：

1. 接收 Telegram 返回的登录数据（`id`, `first_name`, `username`, `auth_date`, `hash` 等）
2. 用 Bot Token 的 SHA256 作为密钥，验证数据的 HMAC 签名
3. 检查 `auth_date` 是否在 1 小时内
4. 验证通过后创建会话并设置 Cookie

### 安全性说明

- Session ID 使用 `secrets.token_hex(32)` 生成（128 位熵）
- Cookie 设置为 `HttpOnly` + `Secure` + `SameSite=Lax`
- 当前使用内存存储 Session，重启后会丢失登录状态
- **生产建议**：改用 JWT Token 或 Redis 存储 Session

---

## 7. 步骤四：创建 Telegram 登录页

创建 `api/telegram_login.html`（Vercel 会将它和其他函数文件一起打包）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>股票智能分析 - 登录</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #0a0e17 0%, #1a1a2e 100%);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: #e2e8f0;
        }
        .login-card {
            background: #111827;
            border: 1px solid #1e293b;
            border-radius: 16px;
            padding: 3rem 2.5rem;
            max-width: 400px;
            width: 90%;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }
        .logo {
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
        }
        h1 {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #f1f5f9;
        }
        .subtitle {
            font-size: 0.9rem;
            color: #64748b;
            margin-bottom: 2rem;
            line-height: 1.5;
        }
        .telegram-login-container {
            display: flex;
            justify-content: center;
            margin-bottom: 1.5rem;
            min-height: 48px;
        }
        .loading-text {
            color: #64748b;
            font-size: 0.85rem;
        }
        .error-msg {
            color: #ef4444;
            font-size: 0.85rem;
            margin-top: 1rem;
            display: none;
        }
        .info-text {
            font-size: 0.75rem;
            color: #475569;
            border-top: 1px solid #1e293b;
            padding-top: 1.5rem;
            margin-top: 1rem;
            line-height: 1.6;
        }
        .info-text a {
            color: #3b82f6;
            text-decoration: none;
        }
        .info-text a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">📈</div>
        <h1>A股智能分析</h1>
        <p class="subtitle">使用 Telegram 账号一键登录</p>

        <div id="tg-login-container" class="telegram-login-container">
            <p class="loading-text">正在加载登录方式...</p>
        </div>

        <p id="error-msg" class="error-msg"></p>

        <div class="info-text">
            登录即表示你同意我们的使用条款。<br />
            首次登录将自动创建你的账号。
        </div>
    </div>

    <script>
        // ── 配置：替换为你的 Bot 信息 ──
        const BOT_USERNAME = "{{TELEGRAM_BOT_USERNAME}}";
        // ──────────────────────────────

        const container = document.getElementById("tg-login-container");
        const errorEl = document.getElementById("error-msg");

        function initTelegramLogin() {
            if (typeof TelegramLoginWidget === "undefined") {
                container.innerHTML = '<p class="loading-text">正在加载 Telegram 登录...</p>';
                return;
            }

            container.innerHTML = "";
            TelegramLoginWidget.create(
                {
                    telegramLogin: BOT_USERNAME,
                    size: "large",
                    radius: 8,
                    onAuth: function(user) {
                        handleTelegramAuth(user);
                    },
                    requestAccess: "write",
                },
                container
            );
        }

        async function handleTelegramAuth(user) {
            try {
                const resp = await fetch("/api/auth/telegram/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(user),
                });

                if (!resp.ok) {
                    const err = await resp.json();
                    showError(err.message || "登录验证失败");
                    return;
                }

                // 登录成功，跳转到主应用
                window.location.href = "/app/";
            } catch (err) {
                showError("网络错误，请重试");
            }
        }

        function showError(msg) {
            errorEl.textContent = msg;
            errorEl.style.display = "block";
        }

        // ── 加载 Telegram Widget 脚本 ──
        const script = document.createElement("script");
        script.src = "https://telegram.org/js/telegram-widget.js?22";
        script.setAttribute("data-telegram-login", BOT_USERNAME);
        script.setAttribute("data-size", "large");
        script.setAttribute("data-radius", "8");
        script.setAttribute("data-onauth", "handleTelegramAuth(user)");
        script.setAttribute("data-request-access", "write");
        script.async = true;
        document.body.appendChild(script);

        // Fallback: 如果脚本加载失败
        setTimeout(() => {
            if (!document.querySelector("iframe[data-telegram-login]") && !document.querySelector(".tgme_widget_login_button")) {
                initTelegramLogin();
            }
        }, 3000);
    </script>
</body>
</html>
```

> **注意**：将文件中的 `{{TELEGRAM_BOT_USERNAME}}` 替换为你的 Bot 用户名，或者在部署前直接用你的值写死。

---

## 8. 步骤五：创建精简依赖文件

Vercel 部署包限制为 50MB。原始 `requirements.txt` 包含大量不需要在 Web 环境下使用的依赖（如钉钉 SDK、Discord SDK、wkhtmltopdf 等）。创建精简版：

创建 `requirements-vercel.txt`：

```txt
# === 核心 ===
python-dotenv>=1.0.0
tenacity>=8.2.0
sqlalchemy>=2.0.0

# === 数据源 ===
akshare>=1.12.0
yfinance>=0.2.0

# === 数据处理 ===
pandas>=2.0.0
numpy>=1.24.0
openpyxl>=3.1.0

# === AI 分析 ===
litellm>=1.80.10
PyYAML>=6.0

# === 搜索引擎 ===
tavily-python>=0.3.0

# === 网络请求 ===
requests>=2.31.0
httpx>=0.27.0

# === Web 框架 ===
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
python-multipart>=0.0.6
jinja2>=3.1.0

# === 其他 ===
markdown2>=2.4.0
pypinyin>=0.50.0
exchange-calendars>=4.5.0
fake-useragent>=1.4.0
```

然后在 `vercel.json` 中指定：

```json
{
  "functions": {
    "api/vercel_app.py": {
      "includeFiles": "**/*",
      "maxDuration": 30
    }
  },
  "rewrites": [...]
}
```

Vercel 会自动使用项目根目录的 `requirements.txt` 安装依赖。如果你不想修改原始文件，可以创建 `vercel.json` 的 `build.command` 字段来指定使用 `requirements-vercel.txt`：

```json
{
  "functions": {
    "api/vercel_app.py": {
      "includeFiles": "**/*",
      "maxDuration": 30
    }
  },
  "rewrites": [...],
  "installCommand": "pip install -r requirements-vercel.txt"
}
```

---

## 9. 步骤六：配置环境变量

### 在 Vercel 控制台中配置

| 环境变量 | 说明 | 必填 |
|---------|------|------|
| `TELEGRAM_BOT_TOKEN` | 从 @BotFather 获取的 Token | ✅ |
| `TELEGRAM_BOT_USERNAME` | Bot 用户名（如 `MyStockBot`） | ✅ |
| `STOCK_LIST` | 自选股列表（逗号分隔） | ✅ |
| `LITELLM_MODEL` | AI 模型名称（如 `gemini/gemini-2.5-flash`） | ✅ |
| `GEMINI_API_KEY` | Gemini API Key（或其它 LLM 的 Key） | ✅ |
| `TUSHARE_TOKEN` | Tushare Token（可选） | |
| `CORS_ALLOW_ALL` | 设为 `true` 放行所有跨域 | ✅ |
| `DATABASE_PATH` | 数据库路径（Vercel 中改用 /tmp/） | ✅ |

### 关于数据库

在 Vercel 中，SQLite 写入后不持久化。**建议方案**：

| 方案 | 说明 | 复杂度 |
|------|------|--------|
| **Neon (PostgreSQL)** | Serverless PostgreSQL，免费版 0.5GB | 低（改连接串即可） |
| **Supabase (PostgreSQL)** | 免费版 500MB 数据库 + 认证 | 低 |
| **用 /tmp/ 勉强用 SQLite** | 临时存储，重启后丢失 | 最简单但不推荐 |
| **Turso (SQLite)** | 边缘托管的 SQLite，Vercel 原生友好 | 中 |

若临时先用 SQLite，在 Vercel 中设置 `DATABASE_PATH=/tmp/stock_analysis.db`。但数据不会持久化。

---

## 10. 步骤七：部署到 Vercel

### 方式 A：通过 GitHub 自动部署（推荐）

1. 将项目推送到 GitHub 仓库
2. 登录 [vercel.com](https://vercel.com)，点击 **Add New → Project**
3. 选择你的仓库
4. 在 **Environment Variables** 中配置所有环境变量
5. 点击 **Deploy**

部署完成后，Vercel 会给你一个 `.vercel.app` 域名。

### 方式 B：通过 Vercel CLI

```bash
# 安装 CLI
npm install -g vercel

# 在项目根目录登录
vercel login

# 部署
vercel --prod
```

CLI 会提示你配置项目和环境变量。

---

## 11. 步骤八：配置 Bot 域名白名单

部署完成后，回到 **@BotFather**：

1. 发送 `/setdomain`
2. 选择你的 Bot
3. 输入你的 Vercel 域名，如 `your-app.vercel.app`

> ⚠️ 必须执行这一步，Telegram Login Widget 才能正常工作。

---

## 12. 用户使用流程

```
用户访问 your-app.vercel.app
        │
        ▼
  看到 Telegram 登录页
        │
        ▼
  点击 "Login with Telegram"
        │
        ▼
  Telegram 弹出授权确认
        │
        ▼
  用户点击 "Authorize"
        │
        ▼
  Telegram 回调到后端验证签名
        │
        ▼
  验证通过 → 设置 Session Cookie
        │
        ▼
  跳转至 /app/ → 加载 SPA 主界面
        │
        ▼
  开始使用股票分析功能
```

---

## 13. 常见问题

### Q: 部署后访问显示 404？

- 检查 `vercel.json` 的 `rewrites` 规则是否正确
- 确保 `api/vercel_app.py` 文件存在且无语法错误
- 查看 Vercel 部署日志：项目页面 → **Deployments** → 点击部署 → **Functions** 标签

### Q: API 返回 504 超时？

- Hobby 计划的函数超时限制为 **10 秒**
- 如果 AI 分析耗时较长，建议升级 Pro 计划或将分析改为异步任务

### Q: Telegram 登录按钮不显示？

- Bot 是否调用了 `setdomain`？
- 检查 `TELEGRAM_BOT_USERNAME` 环境变量是否配置正确
- Telegram Widget 脚本可能被屏蔽，检查浏览器控制台

### Q: 登录后跳转到 `/app/` 又回到登录页？

- 检查 Cookie 是否正确设置
- Vercel 部署在 HTTPS 下，Cookie 的 `Secure` 属性自动启用
- 如果跨域了，确保 CORS 配置允许携带凭证

### Q: 如何让多人在 Vercel 上同时使用？

当前 Telegram Login 允许多人登录。但需要注意：
- 所有用户共享一个 SQLite 数据库（需改用 PostgreSQL）
- 部分分析操作可能互相干扰（建议每个用户使用独立的配置）
- API Key 配额共享（多个用户同时请求可能超过免费限额）

### Q: 如何查看部署日志？

在 Vercel 项目页面：**Deployments** → 点击最新部署 → **Functions** → 选择对应的函数 → **Logs**。

### Q: 开发时如何在本地测试 Telegram 登录？

```bash
# 使用 ngrok 暴露本地服务
ngrok http 8000

# 将 Telegram Bot 的 setdomain 设置为 ngrok 的临时域名
# 然后访问 ngrok 地址测试
```

或者，修改 `api/vercel_app.py` 的 `root()` 路由，添加调试模式：

```python
@app.get("/debug/login", include_in_schema=False)
async def debug_login():
    """开发调试：直接设置 session（生产环境务必移除）"""
    if os.getenv("VERCEL") == "1":
        return JSONResponse(status_code=404, content={"error": "not_available"})
    session_id = _create_session({
        "id": 12345678,
        "first_name": "Dev",
        "username": "dev_user",
    })
    resp = RedirectResponse(url="/app/")
    resp.set_cookie(key=COOKIE_NAME, value=session_id, httponly=True, path="/")
    return resp
```

---

## 附录：项目文件清单

部署后，项目根目录需要包含以下文件：

```
daily_stock_analysis/
├── vercel.json                    # Vercel 配置（新建）
├── requirements-vercel.txt        # 精简依赖（新建）
├── api/
│   ├── vercel_app.py              # Vercel ASGI 入口（新建）
│   ├── telegram_login.html        # Telegram 登录页（新建）
│   ├── __init__.py                # 已有
│   ├── _app.py                    # ⚠️ 重命名自 app.py（避免 Vercel 端点冲突）
│   ├── _deps.py                   # ⚠️ 重命名自 deps.py
│   ├── v1/                        # 已有 API 路由
│   └── middlewares/               # 已有中间件
├── static/                        # 已有前端 SPA 构建产物
├── src/                           # 已有源码
├── server.py                      # 已有（已更新 import）
├── .env                           # 本地开发配置（不提交 Git）
└── data/                          # 本地开发数据
```

> ⚠️ **重要**：Vercel 会将 `api/` 目录下的 `.py` 文件视为独立的 Serverless Function 端点。为了阻止 `app.py` 和 `deps.py` 被错误注册为 API 端点，本项目将其重命名为 `_app.py` 和 `_deps.py`（以下划线开头的文件不会被 Vercel 部署为端点）。所有相关 `import` 已同步更新。
