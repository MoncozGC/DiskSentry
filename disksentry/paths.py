"""路径解析：数据目录与打包后 Web 资源目录的定位。"""
import os
import sys
from pathlib import Path

APP_NAME = "DiskSentry"


def default_data_dir(portable: bool = False) -> Path:
    """数据目录。

    - 打包后的 exe（frozen）：默认就在 exe 同级的 data\\ 下（开箱便携）；
      若该位置不可写（如放在 Program Files），自动降级到 %LOCALAPPDATA%
    - 源码运行：默认 %LOCALAPPDATA%\\DiskSentry；加 --portable 用项目目录的 data\\
    - 任何模式都可用 --data-dir 显式指定
    """
    frozen = getattr(sys, "frozen", False)
    if frozen or portable:
        if frozen:
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parents[1]
        data = base / "data"
        try:
            data.mkdir(parents=True, exist_ok=True)
            probe = data / ".write-test"
            probe.touch()
            probe.unlink()
            return data
        except OSError:
            print(f"警告: 数据目录不可写，改用 %LOCALAPPDATA%\\{APP_NAME}: {data}",
                  file=sys.stderr)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def web_dir() -> Path:
    """Web 静态资源目录：PyInstaller 打包后位于 _MEIPASS/web。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "web"
    return Path(__file__).resolve().parents[1] / "web"
