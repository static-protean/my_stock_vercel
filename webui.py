# -*- coding: utf-8 -*-
"""Very small local Web UI for editing STOCK_LIST in .env.

- Local-only by default (127.0.0.1)
- No external dependencies
- Only edits the STOCK_LIST key; other .env lines are preserved

API Endpoints:
  GET  /           - 配置页面
  GET  /health     - 健康检查
  GET  /analysis?code=xxx - 触发单只股票异步分析

Usage:
  python webui.py
  WEBUI_HOST=0.0.0.0 WEBUI_PORT=8000 python webui.py
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import logging
from typing import Optional, Dict, Any
from datetime import datetime


logger = logging.getLogger(__name__)

# 全局线程池用于异步分析任务
_analysis_executor: Optional[ThreadPoolExecutor] = None
_analysis_tasks: Dict[str, Dict[str, Any]] = {}  # 用于跟踪分析任务状态
_tasks_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """获取或创建全局线程池"""
    global _analysis_executor
    if _analysis_executor is None:
        _analysis_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="analysis_")
    return _analysis_executor


def _run_stock_analysis(code: str) -> Dict[str, Any]:
    """
    执行单只股票分析并推送通知
    
    Args:
        code: 股票代码
        
    Returns:
        分析结果字典
    """
    task_id = f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # 更新任务状态为进行中
    with _tasks_lock:
        _analysis_tasks[task_id] = {
            "code": code,
            "status": "running",
            "start_time": datetime.now().isoformat(),
            "result": None,
            "error": None
        }
    
    try:
        # 延迟导入避免循环依赖
        from config import get_config
        from main import StockAnalysisPipeline
        
        logger.info(f"[WebUI] 开始分析股票: {code}")
        
        # 创建分析管道
        config = get_config()
        pipeline = StockAnalysisPipeline(config=config, max_workers=1)
        
        # 执行单只股票分析（启用单股推送）
        result = pipeline.process_single_stock(
            code=code,
            skip_analysis=False,
            single_stock_notify=True  # 自动推送通知
        )
        
        if result:
            # 分析成功
            result_data = {
                "code": result.code,
                "name": result.name,
                "sentiment_score": result.sentiment_score,
                "operation_advice": result.operation_advice,
                "trend_prediction": result.trend_prediction,
                "analysis_summary": result.analysis_summary,
            }
            
            with _tasks_lock:
                _analysis_tasks[task_id].update({
                    "status": "completed",
                    "end_time": datetime.now().isoformat(),
                    "result": result_data
                })
            
            logger.info(f"[WebUI] 股票 {code} 分析完成: {result.operation_advice}")
            return {"success": True, "task_id": task_id, "result": result_data}
        else:
            # 分析失败
            with _tasks_lock:
                _analysis_tasks[task_id].update({
                    "status": "failed",
                    "end_time": datetime.now().isoformat(),
                    "error": "分析返回空结果"
                })
            
            logger.warning(f"[WebUI] 股票 {code} 分析失败: 返回空结果")
            return {"success": False, "task_id": task_id, "error": "分析返回空结果"}
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[WebUI] 股票 {code} 分析异常: {error_msg}")
        
        with _tasks_lock:
            _analysis_tasks[task_id].update({
                "status": "failed",
                "end_time": datetime.now().isoformat(),
                "error": error_msg
            })
        
        return {"success": False, "task_id": task_id, "error": error_msg}

_ENV_PATH = os.getenv("ENV_FILE", ".env")


def _read_env_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write_env_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


_STOCK_LIST_RE = re.compile(
    r"^(?P<prefix>\s*STOCK_LIST\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)


def _extract_stock_list(env_text: str) -> str:
    for line in env_text.splitlines():
        m = _STOCK_LIST_RE.match(line)
        if m:
            raw = m.group("value").strip()
            # strip surrounding quotes
            if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                raw = raw[1:-1]
            return raw
    return ""


def _normalize_stock_list(value: str) -> str:
    parts = [p.strip() for p in value.replace("\n", ",").split(",")]
    parts = [p for p in parts if p]
    return ",".join(parts)


def _set_stock_list(env_text: str, new_value: str) -> str:
    new_value = _normalize_stock_list(new_value)

    lines = env_text.splitlines(keepends=False)
    out_lines: list[str] = []
    replaced = False

    for line in lines:
        m = _STOCK_LIST_RE.match(line)
        if not m:
            out_lines.append(line)
            continue

        # Preserve prefix spacing; write as plain value (no quotes)
        out_lines.append(f"{m.group('prefix')}{new_value}{m.group('suffix')}")
        replaced = True

    if not replaced:
        # Keep existing text as-is, just append a new key
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.append(f"STOCK_LIST={new_value}")

    # Preserve trailing newline if original had one
    trailing_newline = env_text.endswith("\n") if env_text else True
    out = "\n".join(out_lines)
    return out + ("\n" if trailing_newline else "")


def _page(current_value: str, message: str | None = None) -> bytes:
    safe_value = html.escape(current_value)
    
    # Toast notifications
    toast_html = ""
    if message:
        toast_html = f"""
        <div id="toast" class="toast show">
            <span class="icon">✅</span> {html.escape(message)}
        </div>
        <script>
            setTimeout(() => {{
                document.getElementById('toast').classList.remove('show');
            }}, 3000);
        </script>
        """

    # Modern CSS styling
    css = """
    :root {
        --primary: #2563eb;
        --primary-hover: #1d4ed8;
        --bg: #f8fafc;
        --card: #ffffff;
        --text: #1e293b;
        --text-light: #64748b;
        --border: #e2e8f0;
        --success: #10b981;
    }
    
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        background-color: var(--bg);
        color: var(--text);
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        margin: 0;
        padding: 20px;
    }
    
    .container {
        background: var(--card);
        padding: 2rem;
        border-radius: 1rem;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
        width: 100%;
        max-width: 500px;
    }
    
    h2 {
        margin-top: 0;
        color: var(--text);
        font-size: 1.5rem;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .subtitle {
        color: var(--text-light);
        font-size: 0.875rem;
        margin-bottom: 2rem;
        line-height: 1.5;
    }
    
    .code-badge {
        background: #f1f5f9;
        padding: 0.2rem 0.4rem;
        border-radius: 0.25rem;
        font-family: monospace;
        color: var(--primary);
    }
    
    .form-group {
        margin-bottom: 1.5rem;
    }
    
    label {
        display: block;
        margin-bottom: 0.5rem;
        font-weight: 500;
        color: var(--text);
    }
    
    textarea {
        width: 100%;
        padding: 0.75rem;
        border: 1px solid var(--border);
        border-radius: 0.5rem;
        font-family: monospace;
        font-size: 0.875rem;
        line-height: 1.5;
        resize: vertical;
        box-sizing: border-box;
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    
    textarea:focus {
        outline: none;
        border-color: var(--primary);
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
    }
    
    button {
        background-color: var(--primary);
        color: white;
        border: none;
        padding: 0.75rem 1.5rem;
        border-radius: 0.5rem;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s;
        width: 100%;
        font-size: 1rem;
    }
    
    button:hover {
        background-color: var(--primary-hover);
        transform: translateY(-1px);
    }
    
    button:active {
        transform: translateY(0);
    }
    
    .footer {
        margin-top: 2rem;
        padding-top: 1rem;
        border-top: 1px solid var(--border);
        color: var(--text-light);
        font-size: 0.75rem;
        text-align: center;
    }
    
    /* Toast Notification */
    .toast {
        position: fixed;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%) translateY(100px);
        background: white;
        border-left: 4px solid var(--success);
        padding: 1rem 1.5rem;
        border-radius: 0.5rem;
        box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1);
        display: flex;
        align-items: center;
        gap: 0.75rem;
        transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        opacity: 0;
    }
    
    .toast.show {
        transform: translateX(-50%) translateY(0);
        opacity: 1;
    }
    """

    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>A/H股自选配置 | WebUI</title>
  <style>{css}</style>
</head>
<body>
  <div class="container">
    <h2>📈 A/H股分析配置</h2>
    <div class="subtitle">
        本地配置文件管理 <span class="code-badge">{html.escape(os.path.basename(_ENV_PATH))}</span>
    </div>
    
    <form method="post" action="/update">
      <div class="form-group">
        <label for="stock_list">自选股代码列表</label>
        <textarea 
            id="stock_list" 
            name="stock_list" 
            rows="6" 
            placeholder="例如: 600519, 000001 (支持逗号、换行分隔)"
        >{safe_value}</textarea>
        <div style="font-size: 0.75rem; color: var(--text-light); margin-top: 0.5rem;">
            * 支持输入股票代码，多个代码请用英文逗号或换行分隔
        </div>
      </div>
      <button type="submit">💾 保存配置</button>
    </form>
    
    <div class="footer">
      <p>仅用于本地环境 (127.0.0.1) • 安全修改 .env 配置</p>
    </div>
  </div>
  
  {toast_html}
</body>
</html>"""
    return body.encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # 解析 URL
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        
        # 路由分发
        if path in ("/", ""):
            self._handle_index()
        elif path == "/health":
            self._handle_health()
        elif path == "/analysis":
            self._handle_analysis(query)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
    
    def _handle_index(self) -> None:
        """处理首页请求"""
        env_text = _read_env_text(_ENV_PATH)
        current = _extract_stock_list(env_text)
        payload = _page(current)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    
    def _handle_health(self) -> None:
        """
        健康检查接口
        
        GET /health
        
        返回:
            {
                "status": "ok",
                "timestamp": "2026-01-19T10:30:00",
                "service": "stock-analysis-webui"
            }
        """
        response = {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "service": "stock-analysis-webui"
        }
        self._send_json_response(response)
    
    def _handle_analysis(self, query: Dict[str, list]) -> None:
        """
        触发单只股票异步分析
        
        GET /analysis?code=xxx
        
        参数:
            code: 股票代码（必填）
            
        返回:
            {
                "success": true,
                "message": "分析任务已提交",
                "code": "600519",
                "task_id": "600519_20260119_103000"
            }
        """
        # 获取股票代码参数
        code_list = query.get("code", [])
        if not code_list or not code_list[0].strip():
            self._send_json_response({
                "success": False,
                "error": "缺少必填参数: code (股票代码)"
            }, status=HTTPStatus.BAD_REQUEST)
            return
        
        code = code_list[0].strip()
        
        # 验证股票代码格式（6位数字）
        if not re.match(r'^\d{6}$', code):
            self._send_json_response({
                "success": False,
                "error": f"无效的股票代码格式: {code} (应为6位数字)"
            }, status=HTTPStatus.BAD_REQUEST)
            return
        
        # 提交异步分析任务
        try:
            executor = _get_executor()
            future = executor.submit(_run_stock_analysis, code)
            
            task_id = f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            logger.info(f"[WebUI] 已提交股票 {code} 的分析任务")
            
            self._send_json_response({
                "success": True,
                "message": "分析任务已提交，将异步执行并推送通知",
                "code": code,
                "task_id": task_id
            })
            
        except Exception as e:
            logger.error(f"[WebUI] 提交分析任务失败: {e}")
            self._send_json_response({
                "success": False,
                "error": f"提交任务失败: {str(e)}"
            }, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    
    def _send_json_response(
        self, 
        data: Dict[str, Any], 
        status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        """发送 JSON 响应"""
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/update":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(raw)
        stock_list = form.get("stock_list", [""])[0]

        env_text = _read_env_text(_ENV_PATH)
        updated = _set_stock_list(env_text, stock_list)
        _write_env_text(_ENV_PATH, updated)

        payload = _page(_normalize_stock_list(stock_list), message="已保存")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        # quiet default http.server logging
        return


def run_server_in_thread(host: str = "127.0.0.1", port: int = 8000):
    """Start the WebUI server in a background thread."""
    def serve():
        server = ThreadingHTTPServer((host, port), _Handler)
        logger.info(f"WebUI 已启动: http://{host}:{port}")
        print(f"WebUI 已启动: http://{host}:{port}")
        try:
            server.serve_forever()
        except Exception as e:
            logger.error(f"WebUI 发生错误: {e}")
        finally:
            server.server_close()
            
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


def main() -> int:
    host = os.getenv("WEBUI_HOST", "127.0.0.1")
    port = int(os.getenv("WEBUI_PORT", "8000"))

    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"WebUI running: http://{host}:{port}  (env: {_ENV_PATH})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
