"""Windows 专用能力：进程后台优先级、磁盘枚举、卷标、键鼠空闲时间。

全部通过 ctypes 调用系统 API，不引入第三方依赖；非 Windows 平台上相关函数安全降级。
"""
import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# 文件属性常量（winbase.h）
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# 优先级常量（winbase.h）
PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
PROCESS_MODE_BACKGROUND_END = 0x00200000
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
IDLE_PRIORITY_CLASS = 0x40
NORMAL_PRIORITY_CLASS = 0x20
DRIVE_FIXED = 3


def _kernel32():
    if not IS_WINDOWS:
        return None
    return ctypes.windll.kernel32


class background_priority:
    """扫描期间把进程切到后台模式（CPU + 磁盘 IO 最低优先级），扫描结束恢复。

    与 Windows 索引服务/碎片整理同机制；系统繁忙时扫描自动让路。
    """

    def __init__(self):
        self._applied = False

    def __enter__(self):
        k32 = _kernel32()
        if k32 is None:
            return self
        try:
            handle = k32.GetCurrentProcess()
            if k32.SetPriorityClass(handle, PROCESS_MODE_BACKGROUND_BEGIN):
                self._applied = True
            else:
                # 个别系统不支持 BACKGROUND 模式，退而求其次压低 CPU 优先级
                k32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:  # pragma: no cover - 极端环境下不阻塞扫描
            log.debug("SetPriorityClass 失败，忽略", exc_info=True)
        return self

    def __exit__(self, *exc):
        k32 = _kernel32()
        if k32 is None or not self._applied:
            return False
        try:
            k32.SetPriorityClass(k32.GetCurrentProcess(), PROCESS_MODE_BACKGROUND_END)
        except Exception:
            k32.SetPriorityClass(k32.GetCurrentProcess(), NORMAL_PRIORITY_CLASS)
        return False


def fixed_drives():
    """返回所有固定磁盘根，如 ['C:\\', 'D:\\']。非 Windows 返回空列表。"""
    k32 = _kernel32()
    if k32 is None:
        return []
    drives = []
    try:
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(ord('A') + i)}:\\"
                if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) == DRIVE_FIXED:
                    drives.append(root)
    except Exception:
        log.warning("枚举磁盘失败", exc_info=True)
    return drives


def drive_label(root: str) -> str:
    """取卷标（如 'Windows'、'Data'），失败返回空字符串。"""
    k32 = _kernel32()
    if k32 is None:
        return ""
    try:
        name = ctypes.create_unicode_buffer(261)
        if k32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261,
                                     None, None, None, None, 0):
            return name.value
    except Exception:
        pass
    return ""


def display_label(root: str) -> str:
    """供 UI 展示的盘名：优先卷标；系统盘无卷标时兜底显示"系统盘"。"""
    label = drive_label(root)
    if label:
        return label
    if root.rstrip("\\").upper() == os.environ.get("SystemDrive", "").upper():
        return "系统盘"
    return ""


def idle_seconds() -> float:
    """距上次键鼠输入的秒数；非 Windows 返回 0（视为始终空闲）。"""
    k32 = _kernel32()
    if k32 is None:
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not k32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        elapsed_ms = (ctypes.windll.user32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception:
        return 0.0


def root_exists(root: str) -> bool:
    """监控根是否仍然存在（U 盘拔出、目录改名等情况）。"""
    try:
        return os.path.isdir(root)
    except OSError:
        return False
