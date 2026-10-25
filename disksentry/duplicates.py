"""重复文件查找引擎：MD5 精确分组 + 相似名版本分组 + 回收站删除 + 打开位置。

分组策略（由廉价到昂贵逐级过滤，避免无谓的全量哈希）：
  1. 按文件大小分桶 —— 大小唯一的文件不可能重复，直接排除
  2. 大小相同的桶内先算前 64KB 的部分 MD5 再分桶
  3. 部分 MD5 相同才做全量 MD5 —— 全量一致才算"完全重复"

相似名版本：文件名去掉扩展名后剔除 (1)/副本/copy/数字/分隔符等噪音做归一化，
归一化同名但内容不完全一致的归为"同名版本"组，按修改时间倒序（最新在前）。
"""
import ctypes
import hashlib
import logging
import os
import re
import threading

log = logging.getLogger(__name__)

_PART_SIZE = 64 * 1024   # 部分 MD5 读取的字节数
_CHUNK = 1024 * 1024     # 全量哈希的块大小
_MIN_SIZE = 1            # 跳过 0 字节文件（空文件全部"重复"没有意义）

_NOISE = re.compile(
    r"[\s\-_.·]+|\(\d+\)|（\d+）|\[\d+\]|【\d+】|副本|拷贝|copy|final|最终|新|\d+",
    re.IGNORECASE)

# 下载重名后缀（"(1)"/"- 副本"/"copy" 等）：保留项应优先选不带后缀的原始文件名
_NOISE_MARK = re.compile(r"\(\d+\)|（\d+）|\[\d+\]|【\d+】|副本|拷贝|\bcopy\b", re.IGNORECASE)


def _has_noise(path: str) -> bool:
    return bool(_NOISE_MARK.search(os.path.basename(path)))


def norm_stem(filename: str) -> str:
    """文件名归一化：'照片 (1).jpg' / '照片 - 副本.jpg' -> '照片'。"""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    return _NOISE.sub("", stem)


def _md5(path: str, partial: bool = False) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        if partial:
            h.update(f.read(_PART_SIZE))
        else:
            while True:
                b = f.read(_CHUNK)
                if not b:
                    break
                h.update(b)
    return h.hexdigest()


def scan_directory(root: str, progress_cb=None):
    """扫描 root，返回完全重复组与同名版本组。

    duplicate_groups: [{size, md5, files:[{path, mtime}]}] —— MD5 完全一致
    similar_groups:   [{name, files:[{path, size, mtime, md5}]}] —— 名近内容异，按时间倒序
    """
    from .scanner import iter_files  # 复用遍历器（reparse 跳过/路径净化）

    files = []
    for path, size, mtime in iter_files(root):
        if size >= _MIN_SIZE:
            files.append((path, size, mtime))

    # 1) 按大小分桶，只有大小相同的才可能是重复
    by_size = {}
    for path, size, mtime in files:
        by_size.setdefault(size, []).append((path, mtime))

    # 2)+3) 部分 MD5 分桶 → 全量 MD5 确认
    md5_of = {}   # path -> 全量 md5（候选文件才有）
    dup_map = {}  # (md5, size) -> [(path, mtime)]
    candidates = [g for g in by_size.values() if len(g) > 1]
    total = sum(len(g) for g in candidates)
    done = 0
    for group in candidates:
        partial_buckets = {}
        for path, mtime in group:
            try:
                key = _md5(path, partial=True)
            except OSError:
                key = None
            if key:
                partial_buckets.setdefault(key, []).append((path, mtime))
            done += 1
            if progress_cb and done % 20 == 0:
                progress_cb(done, total, "hash")
        for bucket in partial_buckets.values():
            if len(bucket) < 2:
                continue
            size = os.path.getsize(bucket[0][0]) if bucket else 0
            for path, mtime in bucket:
                try:
                    full = _md5(path)
                except OSError:
                    continue
                md5_of[path] = full
                dup_map.setdefault((full, size), []).append((path, mtime))
    if progress_cb:
        progress_cb(total, total, "hash")

    # 完全重复组排序：无噪音后缀的排前（作为保留项），同类内按时间倒序
    for members in dup_map.values():
        members.sort(key=lambda x: (_has_noise(x[0]), -x[1]))
    duplicate_groups = [
        {"size": size, "md5": md5, "keep": 0,
         "files": [{"path": p, "mtime": t} for p, t in members]}
        for (md5, size), members in dup_map.items() if len(members) > 1
    ]
    # 按可释放空间排序：每组冗余 = size * (n-1)
    duplicate_groups.sort(key=lambda g: -(g["size"] * (len(g["files"]) - 1)))

    # 同名版本：归一化 stem 相同、且内容不完全一致（一致的已在重复组）
    stem_buckets = {}
    for path, size, mtime in files:
        stem = norm_stem(path)
        if not stem or len(stem) > 120:
            continue
        stem_buckets.setdefault(stem, []).append((path, size, mtime))
    similar_groups = []
    for stem, members in stem_buckets.items():
        if len(members) < 2:
            continue
        resolved = []
        for path, size, mtime in members:
            digest = md5_of.get(path)
            if digest is None:  # 未哈希过的补算（同 stem 数量少，成本可控）
                try:
                    digest = _md5(path)
                except OSError:
                    continue
            resolved.append({"path": path, "size": size, "mtime": mtime, "md5": digest})
        if len(resolved) < 2 or len({m["md5"] for m in resolved}) < 2:
            continue  # 内容完全一致 → 已在完全重复分组
        resolved.sort(key=lambda m: -m["mtime"])  # 展示顺序：时间倒序（最新在前）
        # 保留项：优先无噪音后缀的（其中取最新），否则最新
        keep = next((i for i, m in enumerate(resolved) if not _has_noise(m["path"])), 0)
        similar_groups.append({"name": stem, "keep": keep, "files": resolved})
    similar_groups.sort(key=lambda g: -len(g["files"]))

    return {"root": root, "file_count": len(files),
            "duplicate_groups": duplicate_groups,
            "similar_groups": similar_groups}


# ---------------------------------------------------------------- 回收站删除 / 打开位置

FO_DELETE = 3
FOF_ALLOWUNDO = 0x40
FOF_NOCONFIRMATION = 0x10
FOF_SILENT = 0x4
FOF_NOERRORUI = 0x400


def recycle_files(paths):
    """把文件移入回收站（SHFileOperationW，可撤销）。返回 {deleted, failed}。"""

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    shell32 = ctypes.windll.shell32
    deleted, failed = [], []
    for path in paths:
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = str(path) + "\0\0"
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        try:
            rc = shell32.SHFileOperationW(ctypes.byref(op))
            if rc == 0 and not op.fAnyOperationsAborted:
                deleted.append(path)
            else:
                failed.append({"path": path, "error": f"SHFileOperation 返回 {rc}"})
        except Exception as e:  # noqa: BLE001
            failed.append({"path": path, "error": str(e)})
    return {"deleted": deleted, "failed": failed}


def reveal_in_explorer(path: str) -> bool:
    """在资源管理器中打开并选中该文件。

    用 ShellExecuteW 而不是 subprocess：explorer 的 /select 参数解析很老式，
    subprocess 列表参数遇到带空格的路径会把整个 "/select,路径" 加引号包起来，
    explorer 认不出导致打不开；这里手工构造 '/select,"路径"' 的规范形式。
    """
    SW_SHOWNORMAL = 1
    try:
        path = os.path.abspath(path)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "open", "explorer.exe", f'/select,"{path}"', None, SW_SHOWNORMAL)
        return rc > 32
    except Exception:
        log.warning("打开资源管理器失败: %s", path, exc_info=True)
        return False


class DupScanManager:
    """后台扫描管理：一次一个任务，结果留在内存供前端拉取。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._reset()

    def _reset(self, root=None):
        self.running = False
        self.root = root
        self.progress = {"done": 0, "total": 0, "phase": "idle"}
        self.result = None
        self.error = None

    def start(self, root: str) -> bool:
        root = str(root).strip().strip('"')
        with self._lock:
            if self.running:
                return False
            if not os.path.isdir(root):
                raise ValueError(f"目录不存在: {root}")
            self._reset(root)
            self.running = True
            threading.Thread(target=self._run, daemon=True,
                             name="disksentry-dupscan").start()
            return True

    def _run(self):
        def cb(done, total, phase):
            with self._lock:
                self.progress = {"done": done, "total": total, "phase": phase}
        try:
            result = scan_directory(self.root, progress_cb=cb)
            with self._lock:
                self.result = result
                self.progress = {"done": 0, "total": 0, "phase": "done"}
        except Exception as e:  # noqa: BLE001
            log.exception("重复文件扫描失败")
            with self._lock:
                self.error = f"{type(e).__name__}: {e}"
                self.progress = {"done": 0, "total": 0, "phase": "error"}
        finally:
            with self._lock:
                self.running = False

    def drop_deleted(self, paths):
        """删除成功后把对应条目从缓存结果里移除，保持刷新后一致。"""
        gone = set(paths)
        with self._lock:
            r = self.result
            if not r:
                return
            for key in ("duplicate_groups", "similar_groups"):
                kept = []
                for g in r[key]:
                    g["files"] = [f for f in g["files"] if f["path"] not in gone]
                    if len(g["files"]) >= (2 if key == "duplicate_groups" else 1):
                        g["keep"] = min(g.get("keep", 0), len(g["files"]) - 1)
                        kept.append(g)
                r[key] = kept

    def status(self) -> dict:
        with self._lock:
            return {"running": self.running, "root": self.root,
                    "progress": self.progress, "error": self.error,
                    "has_result": self.result is not None}
