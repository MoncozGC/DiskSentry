"""配置：JSON 文件读写，监控根列表支持盘符或任意绝对路径。"""
import json
import logging
import os
import threading
from pathlib import Path

from . import winutil

log = logging.getLogger(__name__)

DEFAULTS = {
    "disks": None,                    # None = 首次运行自动选择所有固定磁盘
    "interval_minutes": 60,           # 扫描间隔
    "alert_threshold_gb_per_hour": 5.0,  # 增长告警阈值（GB/小时）
    "port": 8765,
    "only_when_idle": False,          # 仅在键鼠空闲时扫描
    "idle_wait_minutes": 10,
    "retention_days": 90,             # 变更明细保留天数
    "exclude": [],                    # 排除的路径前缀列表
}

_INT_KEYS = {"interval_minutes", "port", "idle_wait_minutes", "retention_days"}
_FLOAT_KEYS = {"alert_threshold_gb_per_hour"}
_BOOL_KEYS = {"only_when_idle"}


def normalize_root(path: str) -> str:
    """规范化监控根：'C:' -> 'C:\\'；普通目录 normpath 去掉尾部斜杠。"""
    p = str(path).strip()
    if len(p) == 2 and p[1] == ":":
        return p + "\\"
    p = os.path.normpath(p)
    if len(p) == 3 and p[1] == ":" and p.endswith("\\"):
        return p
    return p


class Config:
    """线程安全的配置对象。scheduler/server 每次循环读取最新快照。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        with self._lock:
            data = dict(DEFAULTS)
            try:
                if self.path.exists():
                    with open(self.path, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
            except (OSError, ValueError):
                log.warning("配置文件损坏，使用默认值: %s", self.path, exc_info=True)
            if not data["disks"]:
                data["disks"] = winutil.fixed_drives()
            data["disks"] = [normalize_root(d) for d in data["disks"] if str(d).strip()]
            data["exclude"] = [normalize_root(e) for e in (data.get("exclude") or [])]
            self._data = data

    @property
    def data(self) -> dict:
        with self._lock:
            return dict(self._data)

    def update(self, changes: dict):
        """校验并应用部分配置，原子写回文件。返回合并后的完整配置。"""
        with self._lock:
            merged = dict(self._data)
            for key, value in (changes or {}).items():
                if key not in DEFAULTS:
                    continue
                if key in _INT_KEYS:
                    value = int(value)
                elif key in _FLOAT_KEYS:
                    value = float(value)
                elif key in _BOOL_KEYS:
                    value = bool(value)
                if key == "disks":
                    value = [normalize_root(d) for d in value if str(d).strip()]
                if key == "exclude":
                    value = [normalize_root(e) for e in value if str(e).strip()]
                merged[key] = value
            merged["disks"] = [normalize_root(d) for d in merged["disks"] if str(d).strip()]
            merged["exclude"] = [normalize_root(e) for e in (merged.get("exclude") or []) if str(e).strip()]
            tmp = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._data = merged
            return dict(merged)
