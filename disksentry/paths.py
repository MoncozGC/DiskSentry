"""路径解析：数据目录与打包后 Web 资源目录的定位。"""
import os
import sys
from pathlib import Path

APP_NAME = "DiskSentry"


def default_data_dir(portable: bool = False) -> Path:
    """数据目录：默认 %LOCALAPPDATA%\\DiskSentry；--portable 时放在程序旁边。"""
    if portable:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parents[1]
        return base / "data"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def web_dir() -> Path:
    """Web 静态资源目录：PyInstaller 打包后位于 _MEIPASS/web。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "web"
    return Path(__file__).resolve().parents[1] / "web"
