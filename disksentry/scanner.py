"""目录扫描器：os.scandir 流式遍历，跳过 reparse point（junction/符号链接）。"""
import logging
import os
import threading
from stat import S_ISDIR

from . import winutil

log = logging.getLogger(__name__)

_BATCH = 4096


class ScanStatus:
    """扫描进度的线程安全快照，供 Web UI 展示。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"running": False, "current_root": None, "entries": 0,
                       "started_at": None, "last_finished": None, "last_error": None}

    def begin(self):
        import time
        with self._lock:
            self._state = {"running": True, "current_root": None, "entries": 0,
                           "started_at": time.time(),
                           "last_finished": self._state.get("last_finished"),
                           "last_error": self._state.get("last_error")}

    def update(self, root: str = None, entries: int = None):
        with self._lock:
            if root is not None:
                self._state["current_root"] = root
            if entries is not None:
                self._state["entries"] = entries

    def finish(self, error: str = None):
        import time
        with self._lock:
            self._state.update({"running": False, "current_root": None,
                                "last_finished": time.time(), "last_error": error})

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


def _excluded(path: str, excl_norm) -> bool:
    if not excl_norm:
        return False
    p = os.path.normcase(path)
    for ex in excl_norm:
        if p == ex or p.startswith(ex + "\\"):
            return True
    return False


def _sanitize(path: str) -> str:
    """Windows 允许文件名含未配对的代理字符（无法编码为 UTF-8），替换为 ? 保证可入库。"""
    try:
        path.encode("utf-8")
        return path
    except UnicodeEncodeError:
        return path.encode("utf-8", "replace").decode("utf-8")


def iter_files(root: str, excludes=(), counter=None):
    """深度优先遍历 root，产出 (path, size, mtime)。

    - 跳过所有 reparse point（符号链接/junction），避免循环与重复计数
    - 访问被拒的目录/文件静默跳过并计数
    - counter: dict，可传入以接收 skipped/errors 统计
    """
    stats = counter if counter is not None else {}
    excl_norm = [os.path.normcase(os.fspath(e)) for e in excludes]
    stack = [root]
    n = 0
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        try:
            with it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        st = entry.stat(follow_symlinks=False)
                        if getattr(st, "st_file_attributes", 0) & winutil.FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if S_ISDIR(st.st_mode):
                            if not _excluded(entry.path, excl_norm):
                                stack.append(entry.path)
                            continue
                        yield _sanitize(entry.path), st.st_size, st.st_mtime
                        n += 1
                        if n % 5000 == 0:
                            stats["entries"] = n
                    except OSError:
                        stats["errors"] = stats.get("errors", 0) + 1
        except OSError:
            stats["skipped"] = stats.get("skipped", 0) + 1
    stats["entries"] = n


def scan_root(conn, root: str, ts: float, excludes=(), status: ScanStatus = None):
    """扫描单个根并落库，返回统计 dict。"""
    from . import store
    counter = {}

    def gen():
        for i, entry in enumerate(iter_files(root, excludes, counter)):
            if status is not None and i % 20000 == 0:
                status.update(root=root, entries=counter.get("entries", i))
            yield entry

    result = store.apply_scan(conn, root, ts, gen())
    result.update({"skipped": counter.get("skipped", 0), "errors": counter.get("errors", 0)})
    if status is not None:
        status.update(root=root, entries=result["file_count"])
    return result
