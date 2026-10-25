"""本地 Web 服务：静态仪表盘 + JSON API，仅绑定 127.0.0.1。"""
import json
import logging
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, duplicates, store, winutil
from .paths import web_dir

log = logging.getLogger(__name__)

_GB = 1024 ** 3
_WINDOWS_HOURS = {1, 24, 168, 720, 8760}


class _Ctx:
    def __init__(self, cfg, scheduler, status, db_path):
        self.cfg = cfg
        self.scheduler = scheduler
        self.status = status
        self.db_path = db_path
        self.dup = duplicates.DupScanManager()


class Handler(BaseHTTPRequestHandler):
    server_version = f"DiskSentry/{__version__}"

    # ---------------------------------------------------------- helpers
    @property
    def ctx(self) -> _Ctx:
        return self.server.ctx

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message):
        self._json({"error": message}, code)

    def _db(self):
        return store.connect(self.ctx.db_path)

    @staticmethod
    def _window_hours(params) -> int:
        try:
            h = int(params.get("window", ["24"])[0])
        except ValueError:
            raise ValueError("window 必须是整数小时")
        if h < 1 or h > 8760 * 2:
            raise ValueError("window 超出范围")
        return h

    # ---------------------------------------------------------- routing
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/" or path == "/index.html":
                return self._static("index.html")
            if path.startswith("/api/"):
                params = parse_qs(parsed.query)
                return self._api_get(path, params)
            if path.startswith("/vendor/") or path in (
                    "/app.js", "/dup.js", "/style.css", "/favicon.svg",
                    "/favicon.png", "/dup.html"):
                return self._static(path.lstrip("/"))
            return self._error(404, "not found")
        except ValueError as e:
            return self._error(400, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("GET %s 处理失败", path)
            return self._error(500, f"{type(e).__name__}: {e}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            # 批量删除的路径列表可能很大（数千个长路径，JSON 转义反斜杠翻倍），单独放宽
            limit = 5 * 1024 * 1024 if path == "/api/dup/delete" else 1024 * 1024
            if length > limit:
                return self._error(413, "请求体过大")
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/config":
                return self._post_config(body)
            if path == "/api/scan":
                return self._post_scan()
            if path == "/api/dup/scan":
                return self._post_dup_scan(body)
            if path == "/api/dup/delete":
                return self._post_dup_delete(body)
            if path == "/api/dup/reveal":
                return self._post_dup_reveal(body)
            return self._error(404, "not found")
        except (ValueError, json.JSONDecodeError) as e:
            return self._error(400, f"参数错误: {e}")
        except Exception as e:  # noqa: BLE001
            log.exception("POST %s 处理失败", path)
            return self._error(500, f"{type(e).__name__}: {e}")

    # ---------------------------------------------------------- static
    def _static(self, rel: str):
        base = web_dir().resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)):
            return self._error(403, "forbidden")
        try:
            data = target.read_bytes()
        except OSError:
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------- API GET
    def _api_get(self, path, params):
        conn = self._db()
        try:
            if path == "/api/state":
                return self._json(self._state(conn))
            if path == "/api/summary":
                hours = self._window_hours(params)
                return self._json(self._summary(conn, hours, params))
            if path == "/api/trend":
                hours = self._window_hours(params)
                since = time.time() - hours * 3600
                return self._json({"window_hours": hours, "series": store.trend(conn, since)})
            if path == "/api/changes":
                hours = self._window_hours(params)
                since = time.time() - hours * 3600
                order = params.get("order", ["ts"])[0]
                if order not in ("ts", "delta"):
                    return self._error(400, "order 仅支持 ts/delta")
                limit = min(int(params.get("limit", ["100"])[0]), 500)
                offset = max(int(params.get("offset", ["0"])[0]), 0)
                root = params.get("root", [None])[0]
                rows = store.recent_changes(conn, since, order=order, limit=limit,
                                            offset=offset, root=root)
                return self._json({"window_hours": hours, "changes": rows})
            if path == "/api/dup/status":
                return self._json(self.ctx.dup.status())
            if path == "/api/dup/result":
                st = self.ctx.dup.status()
                if st.get("error"):
                    return self._error(500, st["error"])
                if not st.get("has_result"):
                    return self._error(404, "尚未完成扫描")
                return self._json(self.ctx.dup.result)
            return self._error(404, "not found")
        finally:
            conn.close()

    def _state(self, conn, ):
        cfg = self.ctx.cfg.data
        available = []
        seen = set()
        for root in winutil.fixed_drives() + cfg["disks"]:
            if root in seen:
                continue
            seen.add(root)
            item = {"root": root, "label": winutil.drive_label(root)}
            try:
                import shutil
                usage = shutil.disk_usage(root)
                item.update({"total": usage.total,
                             "used": usage.total - usage.free,
                             "free": usage.free})
            except OSError:
                item.update({"total": None, "used": None, "free": None})
            available.append(item)
        last = store.last_scan(conn)
        return {
            "version": __version__,
            "available": available,
            "config": cfg,
            "scanning": self.ctx.status.snapshot(),
            "last_scan": last,
            "alerts": store.recent_alerts(conn, 10),
            "db_size": store.db_size(self.ctx.db_path),
        }

    def _summary(self, conn, hours, params):
        since = time.time() - hours * 3600
        root = params.get("root", [None])[0]
        try:
            topn = int(params.get("topn", ["8"])[0])
        except ValueError:
            raise ValueError("topn 必须是整数")
        topn = max(1, min(topn, 50))
        deltas = store.window_deltas(conn, since)
        dirs, files = store.top_dirs(conn, since, topn=topn, root=root)
        changes = store.recent_changes(conn, since, order="ts", limit=50, root=root)
        return {"window_hours": hours,
                "deltas": deltas,
                "top_dirs": dirs,
                "top_files": files,
                "changes": changes}

    # ---------------------------------------------------------- API POST
    def _post_config(self, body):
        if not isinstance(body, dict):
            return self._error(400, "请求体必须是 JSON 对象")
        merged = self.ctx.cfg.update(body)
        return self._json({"ok": True, "config": merged})

    def _post_scan(self):
        if self.ctx.status.snapshot().get("running"):
            return self._json({"ok": False, "detail": "扫描进行中"}, 409)
        if self.ctx.scheduler is None:
            return self._error(400, "当前模式不支持触发扫描")
        self.ctx.scheduler.request_scan()
        return self._json({"ok": True}, 202)

    # ---------------------------------------------------------- 重复文件
    def _post_dup_scan(self, body):
        root = (body or {}).get("root") if isinstance(body, dict) else None
        if not root or not isinstance(root, str):
            return self._error(400, "缺少 root 参数")
        try:
            started = self.ctx.dup.start(root)
        except ValueError as e:
            return self._error(400, str(e))
        if not started:
            return self._json({"ok": False, "detail": "已有扫描进行中"}, 409)
        return self._json({"ok": True}, 202)

    def _post_dup_delete(self, body):
        paths = (body or {}).get("paths") if isinstance(body, dict) else None
        if not isinstance(paths, list) or not paths:
            return self._error(400, "缺少 paths 列表")
        if len(paths) > 5000:
            return self._error(400, "单次最多删除 5000 个文件")
        for p in paths:
            if not isinstance(p, str) or not os.path.isabs(p):
                return self._error(400, f"非法路径: {p!r}")
        result = duplicates.recycle_files(paths)
        self.ctx.dup.drop_deleted(result["deleted"])
        return self._json(result)

    def _post_dup_reveal(self, body):
        path = (body or {}).get("path") if isinstance(body, dict) else None
        if not path or not isinstance(path, str) or not os.path.isabs(path):
            return self._error(400, "非法路径")
        if not os.path.exists(path):
            return self._error(404, "文件不存在")
        ok = duplicates.reveal_in_explorer(path)
        return self._json({"ok": ok})


def make_server(cfg, scheduler, status, db_path, port: int):
    """创建 HTTP 服务；端口被占用时向后尝试最多 10 个端口。"""
    last_err = None
    for p in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            httpd.ctx = _Ctx(cfg, scheduler, status, db_path)
            return httpd
        except OSError as e:
            last_err = e
    raise RuntimeError(f"无法绑定端口 {port}-{port + 9}: {last_err}")
