"""PyInstaller 打包入口：以包形式启动 disksentry。"""
from disksentry.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
