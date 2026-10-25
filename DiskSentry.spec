# -*- mode: python ; coding: utf-8 -*-
# DiskSentry 打包配置：单文件 exe，带控制台窗口（可看到扫描日志，关窗即退出）
# 如需无窗口版本，把下面 console=True 改为 False 后重新打包
a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('web', 'web')],   # 仪表盘静态资源（含本地 d3）
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc_data'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DiskSentry',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,        # 改为 False 即无窗口后台运行（退出需任务管理器）
    icon='assets/icon.ico',
)
