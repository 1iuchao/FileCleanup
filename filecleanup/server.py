"""本地 HTTP 服务层：REST API + 静态资源。

只监听 127.0.0.1，扫描在后台线程执行，HTTP 线程仅读取进度快照，
因此界面轮询不会阻塞扫描，也不会卡死。
"""

from __future__ import annotations

import heapq
import json
import mimetypes
import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import extmap
from .config import (
    CHILD_PAGE_SIZE,
    DEFAULT_PORT,
    DEFAULT_TREE_DEPTH,
    HOST,
    PORT_FALLBACK_TRIES,
    TOP_N_DEFAULT,
    VERSION,
    WEB_DIR,
)
from .deleter import delete_path
from .scanner import Scanner
from .system_ops import open_in_explorer

MIME_FIX = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class AppState:
    """全局单例状态（整个进程一次只维护一棵树）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.scanner: Optional[Scanner] = None
        self.root_path: str = ""
        self._top_cache: Optional[dict] = None

    def new_scan(self, path: str) -> Scanner:
        with self.lock:
            if self.scanner is not None and self.scanner.state.running:
                raise RuntimeError("已有扫描任务正在运行，请先等待或取消")
            self.scanner = Scanner()
            self.root_path = os.path.abspath(os.path.expanduser(path))
            self._top_cache = None
            self.scanner.start(self.root_path)
            return self.scanner

    def top(self, n: int) -> dict:
        """体积最大的 N 个文件 / 目录（结果缓存，避免重复排序）。"""
        with self.lock:
            if self._top_cache and self._top_cache.get("n") == n:
                return self._top_cache
            scanner = self.scanner
            if scanner is None or not scanner.nodes:
                return {"files": [], "dirs": [], "n": n}
            files, dirs = [], []
            root = scanner.root
            for node in scanner.nodes:
                if node is root:
                    continue
                (dirs if node.is_dir else files).append(node)
            key = lambda x: x.size                                   # noqa: E731
            top_files = heapq.nlargest(n, files, key=key)
            top_dirs = heapq.nlargest(n, dirs, key=key)
            self._top_cache = {
                "n": n,
                "files": [x.to_dict(depth=0) for x in top_files],
                "dirs": [x.to_dict(depth=0) for x in top_dirs],
            }
            return self._top_cache


STATE = AppState()
HTTPD = None          # 当前监听中的服务实例，供 /api/shutdown 关闭
SHUTTING_DOWN = False


def request_shutdown(delay: float = 0.35) -> None:
    """请求关闭服务：先中断扫描，再停掉监听循环。

    shutdown() 不能在请求线程里直接调用（会与 serve_forever 互等死锁），
    所以这里丢到一个独立线程里，先把响应发回去再关。
    """
    global SHUTTING_DOWN
    if SHUTTING_DOWN:
        return
    SHUTTING_DOWN = True

    def _do():
        time.sleep(delay)                       # 留出把 HTTP 响应发完的时间
        try:
            scanner = STATE.scanner
            if scanner is not None and scanner.state.running:
                scanner.cancel()
                time.sleep(0.2)
        except Exception:
            pass
        httpd = HTTPD
        if httpd is not None:
            try:
                httpd.shutdown()                # 让 serve_forever() 返回
            except Exception:
                pass

    threading.Thread(target=_do, name="shutdown", daemon=True).start()


def _quick_roots() -> list:
    """供前端"快速选择"下拉使用的常用起点（盘符 / 用户目录）。"""
    items = []
    home = os.path.expanduser("~")
    for label, p in (("用户主目录", home),
                     ("桌面", os.path.join(home, "Desktop")),
                     ("下载", os.path.join(home, "Downloads")),
                     ("文档", os.path.join(home, "Documents"))):
        if os.path.isdir(p):
            items.append({"label": label, "path": p})
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                items.append({"label": f"本地磁盘 ({letter}:)", "path": drive})
    else:
        if os.path.isdir("/"):
            items.append({"label": "根目录 /", "path": "/"})
    return items


class Handler(BaseHTTPRequestHandler):
    server_version = "FileCleanup/0.1"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- 基础工具
    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _file(self, rel: str, code: int = 200) -> None:
        safe = rel.split("?")[0].lstrip("/")
        target = (WEB_DIR / safe).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.send_error(404)
            return
        ctype = MIME_FIX.get(target.suffix.lower()) or mimetypes.guess_type(
            str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        # 第三方库（vendor/）内容基本不变，允许浏览器长期缓存；业务文件保持 no-cache
        cache = "public, max-age=604800" if "/vendor/" in str(target).replace("\\", "/") else "no-cache"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:      # 静音高频轮询
        msg = fmt % args
        if "/api/progress" in msg:
            return
        super().log_message(fmt, *args)

    # ---------------------------------------------------------------- 路由
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)

        if path == "/" or path == "/index.html":
            return self._file("index.html")

        if path.startswith("/api/"):
            return self._api_get(path, query)

        if path.startswith("/static/"):
            return self._file(unquote(path[len("/static/"):]))
        return self._file(unquote(path.lstrip("/")))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._body()

        if path == "/api/scan":
            target = (data.get("path") or "").strip()
            if not target:
                return self._json({"ok": False, "error": "缺少扫描路径"}, 400)
            if not os.path.exists(target):
                return self._json({"ok": False, "error": f"路径不存在：{target}"}, 400)
            try:
                STATE.new_scan(target)
            except RuntimeError as exc:
                return self._json({"ok": False, "error": str(exc)}, 409)
            return self._json({"ok": True, "root": STATE.root_path})

        if path == "/api/cancel":
            if STATE.scanner:
                STATE.scanner.cancel()
            return self._json({"ok": True})

        if path == "/api/shutdown":
            # 界面上的「退出」：停止扫描 → 关闭监听 → 进程退出（端口立即释放）
            return self._handle_shutdown()

        if path == "/api/open":
            target = (data.get("path") or "").strip()
            if not target or not os.path.lexists(target):
                return self._json({"ok": False, "error": "路径不存在"}, 400)
            open_in_explorer(target)
            return self._json({"ok": True})

        if path == "/api/delete":
            return self._handle_delete(data)

        return self._json({"ok": False, "error": "未知接口"}, 404)

    # ---------------------------------------------------------------- GET API
    def _api_get(self, path: str, query: dict) -> None:
        scanner = STATE.scanner

        if path == "/api/health":
            return self._json({"ok": True, "version": VERSION})

        if path == "/api/extmap":
            return self._json({
                "ok": True,
                "table": extmap.table(),
                "categories": extmap.CATEGORY_LABELS,
            })

        if path == "/api/roots":
            return self._json({"ok": True, "roots": _quick_roots()})

        if path == "/api/top":
            if STATE.scanner is None or not STATE.scanner.nodes:
                return self._json({"ok": False, "error": "尚未扫描"}, 400)
            try:
                n = int((query.get("n") or [TOP_N_DEFAULT])[0])
            except ValueError:
                n = TOP_N_DEFAULT
            n = max(1, min(n, 500))
            data = STATE.top(n)
            return self._json({"ok": True, "files": data["files"], "dirs": data["dirs"]})

        if path == "/api/progress":
            if scanner is None:
                return self._json({"ok": True, "running": False, "finished": False})
            snap = scanner.snapshot()
            snap["ok"] = True
            return self._json(snap)

        if path == "/api/tree":
            if scanner is None or not scanner.nodes:
                return self._json({"ok": False, "error": "尚未扫描"}, 400)
            depth = int((query.get("depth") or [DEFAULT_TREE_DEPTH])[0])
            root = scanner.root
            return self._json({
                "ok": True,
                # 每层都限流，避免把上万个孙节点一次性塞进首屏响应
                "root": root.to_dict(depth=depth, max_children=CHILD_PAGE_SIZE),
                "total": {"size": root.size, "files": scanner.state.files,
                          "dirs": scanner.state.dirs, "nodes": len(scanner.nodes)},
            })

        if path == "/api/children":
            if scanner is None:
                return self._json({"ok": False, "error": "尚未扫描"}, 400)
            try:
                node_id = int((query.get("id") or [0])[0])
            except ValueError:
                return self._json({"ok": False, "error": "参数错误"}, 400)
            node = scanner.node(node_id)
            if node is None:
                return self._json({"ok": False, "error": "节点不存在"}, 404)
            # 分页取子节点：单个目录下几万条目时不会一次性灌进浏览器
            try:
                offset = int((query.get("offset") or [0])[0])
                limit = int((query.get("limit") or [CHILD_PAGE_SIZE])[0])
            except ValueError:
                offset, limit = 0, CHILD_PAGE_SIZE
            offset, limit = max(0, offset), max(1, min(limit, 2000))
            kids = node.children[offset:offset + limit]
            return self._json({
                "ok": True,
                "children": [c.to_dict(depth=0) for c in kids],
                "offset": offset,
                "limit": limit,
                "total": node.child_count,
                "hasMore": offset + limit < node.child_count,
            })

        if path == "/api/node":
            if scanner is None:
                return self._json({"ok": False, "error": "尚未扫描"}, 400)
            try:
                node_id = int((query.get("id") or [-1])[0])
            except ValueError:
                return self._json({"ok": False, "error": "参数错误"}, 400)
            node = scanner.node(node_id)
            if node is None:
                return self._json({"ok": False, "error": "节点不存在"}, 404)
            parent = node.parent
            info = node.to_dict(depth=0)
            info["parent"] = parent.path if parent else None
            if node.is_dir:
                info["extZh"], info["cat"] = "文件夹", "dir"
            else:
                zh, cat = extmap.describe(node.ext)
                info["extZh"], info["cat"] = zh, cat
            return self._json({"ok": True, "node": info})

        return self._json({"ok": False, "error": "未知接口"}, 404)

    def _handle_shutdown(self) -> None:
        self._json({"ok": True, "message": "服务正在关闭"})
        request_shutdown()

    # ---------------------------------------------------------------- 删除
    def _handle_delete(self, data: dict) -> None:
        scanner = STATE.scanner
        if scanner is None or not scanner.nodes:
            return self._json({"ok": False, "error": "尚未扫描"}, 400)

        ids = data.get("ids") or []
        use_recycle = bool(data.get("useRecycle", True))
        if not ids:
            return self._json({"ok": False, "error": "待删除列表为空"}, 400)

        root = os.path.abspath(STATE.root_path)
        results, freed = [], 0
        for raw_id in ids:
            try:
                node_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            node = scanner.node(node_id)
            if node is None:
                results.append({"path": str(raw_id), "ok": False, "method": "none",
                                "message": "节点不存在"})
                continue
            # 安全边界：只允许删除本次扫描树内、且位于扫描根路径之下的条目
            target = os.path.abspath(node.path)
            if os.path.commonpath([root, target]) != root:
                results.append({"path": target, "ok": False, "method": "none",
                                "message": "超出本次扫描范围，已拒绝"})
                continue
            res = delete_path(target, use_recycle=use_recycle)
            if res["ok"]:
                freed += node.size
            results.append(res)
        return self._json({"ok": True, "results": results, "freed": freed})


class LocalServer(ThreadingHTTPServer):
    """仅本地回环的 HTTP 服务。

    Windows 下 SO_REUSEADDR 允许多个进程绑定同一个监听端口（会互相抢连接），
    所以这里显式关掉，保证"第二个实例自动换端口"能真正生效。
    """

    allow_reuse_address = False
    daemon_threads = True


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    """启动本地服务（阻塞）。端口被占用时自动向后顺延。"""
    extmap.table()                       # 预热：首屏请求不必再读 JSON

    httpd = None
    for i in range(PORT_FALLBACK_TRIES):
        candidate = port + i
        if not _port_free(candidate):
            continue
        try:
            httpd = LocalServer((HOST, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print(f"[FileCleanup] 端口 {port} ~ {port + PORT_FALLBACK_TRIES - 1} 均被占用，启动失败")
        return

    global HTTPD
    HTTPD = httpd
    url = f"http://{HOST}:{port}/"
    print(f"[FileCleanup] 服务已启动：{url}   (Ctrl+C 或点界面上的「退出」即可关闭)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[FileCleanup] 已退出")
    finally:
        HTTPD = None
        httpd.server_close()
        print("[FileCleanup] 服务已停止，端口已释放")
