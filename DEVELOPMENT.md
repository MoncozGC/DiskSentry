# DiskSentry 开发文档

面向后续开发者（包括未来的你自己）：读完本文即可了解项目全貌、搭好环境、开始改代码。
使用手册请看 [README.md](README.md)。

---

## 1. 项目概览

DiskSentry 解决"磁盘莫名上涨，找不到是哪个文件导致的"这个问题。

核心思路：**周期性全量快照扫描 + 差分**，而不是实时监听。

```
┌──────────┐   定时触发    ┌───────────┐  (path,size,mtime)  ┌──────────────┐
│ Scheduler │ ──────────▶ │  Scanner   │ ─────────────────▶ │    Store      │
│ (调度线程) │             │ (目录遍历)  │     流式产出        │ (SQLite 差分)  │
└──────────┘             └───────────┘                    └──────┬───────┘
      │                   扫描期间进程切换为                        │ 变更事件/趋势
      │                   Windows 后台优先级                        ▼
      │                                                    ┌──────────────┐
      └──── 增速超阈值 → 告警(alerts) ──────────────────────▶│    Server     │
                                     JSON API + 静态页      │ (127.0.0.1)   │
                                                           └──────┬───────┘
                                                                  ▼
                                                          web/ 仪表盘 (d3)
```

核心功能：

| 功能 | 实现位置 |
| --- | --- |
| 定时扫描所有监控盘（默认全盘，可勾选） | `scheduler.py` |
| 目录遍历：跳过符号链接/junction、无权限静默跳过、路径净化 | `scanner.py` |
| 快照差分：新建/删除/增长/缩小四类事件，SQL 集合运算完成 | `store.py::apply_scan` |
| 任意时间窗回溯：各盘净增量、Top 目录/文件、变更明细 | `store.py` 查询函数 |
| 增长告警（GB/小时阈值） | `scheduler.py::_check_alert` |
| Web 仪表盘：趋势图/排行/明细表/设置对话框 | `web/` + `server.py` |
| CLI：run / scan / report / config | `__main__.py` |
| 低负载保证：后台优先级 + 可选空闲扫描 + 数据保留策略 | `winutil.py` / `scheduler.py` |

---

## 2. 目录结构与文件用途

```
monitor/
├── run.py                  ★ PyInstaller 打包入口（必须是它，见 §6.3）
├── DiskSentry.spec         PyInstaller 配置（单文件/控制台开关/打包资源）
├── build.bat               一键打包脚本（自动维护 .venv-build）
├── requirements-build.txt  仅打包需要的依赖（pyinstaller）；运行时零依赖
├── README.md               使用手册
├── DEVELOPMENT.md          本文档
│
├── disksentry/             Python 包（全部逻辑）
│   ├── __init__.py         版本号
│   ├── __main__.py         CLI 入口：参数解析、日志初始化、命令分发
│   ├── config.py           配置：JSON 读写、线程安全、默认值、根路径规范化
│   ├── paths.py            数据目录与打包后 Web 资源目录的定位
│   ├── winutil.py          ctypes 调 Windows API：后台优先级/磁盘枚举/卷标/空闲检测
│   ├── scanner.py          目录遍历器 + ScanStatus 进度上报
│   ├── store.py            ★ SQLite：建表、快照差分、全部查询、清理
│   ├── scheduler.py        调度循环、单根失败隔离、告警评估、保留策略
│   ├── server.py           本地 HTTP 服务：静态页 + JSON API（只绑 127.0.0.1）
│   └── report.py           终端文字报告（report 命令）
│
├── web/                    前端（无构建步骤，改完刷新即生效）
│   ├── index.html          页面骨架（顶栏/卡片/图表/排行/明细/设置对话框）
│   ├── app.js              ★ 全部前端逻辑：API 拉取、渲染、d3 趋势图、交互
│   ├── style.css           样式，跟随系统深浅色（prefers-color-scheme）
│   ├── favicon.svg/png     网页图标（由 tools/make_icon.py 生成）
│   └── vendor/d3.min.js    本地 d3 7.9.0（离线可用，勿升级路径写法）
│
├── assets/                 图标资源（icon.ico 供打包，icon.svg 矢量原稿）
├── tools/
│   └── make_icon.py        图标生成脚本（Pillow，一次性资产生成，非运行时依赖）
│
├── tests/                  单元测试（unittest，见 §5）
│   ├── test_store.py       差分算法 / 查询 / 清理
│   ├── test_scanner.py     遍历 / 排除 / 计数
│   └── test_server.py      API 全链路（含真实扫描两轮制造差分）
│
├── .venv-build/            打包专用虚拟环境（git 忽略，build.bat 自动创建）
├── build/ dist/            PyInstaller 中间产物与 exe（git 忽略）
└── data/ data-dev/         便携模式/开发调试的数据目录（git 忽略）
```

★ 标记的是改动频率最高、最值得先读的文件。

### 2.1 运行时数据目录（默认 `%LOCALAPPDATA%\DiskSentry\`）

| 文件 | 内容 |
| --- | --- |
| `config.json` | 配置（见 README §配置） |
| `data.db` | SQLite 数据库（WAL 模式；另有 `data.db-wal/-shm` 临时文件属正常） |
| `disksentry.log` | 运行日志，2MB×2 自动轮转 |

### 2.2 数据库表结构（`store.py::SCHEMA`）

| 表 | 用途 |
| --- | --- |
| `files` | 当前文件状态快照（root+path 主键，size/mtime/首末次见到时间） |
| `changes` | ★ 变更日志（ts/root/path/kind/old/new/delta），一切查询的源头 |
| `scans` | 每轮扫描的元信息（状态 ok/partial/error、耗时、增删改计数） |
| `disk_snapshots` | 每轮扫描记录的卷级用量（趋势图数据源） |
| `alerts` | 增速告警记录 |
| `meta` | 预留 |

---

## 3. 环境搭建

**运行与开发都不需要安装任何第三方库**（纯标准库），这是刻意的设计约束，请保持。

- 硬性要求：Windows 10+；Python **3.10+**（开发验证版本；语法上兼容 3.8+，未逐一测试）
- 可选但建议：开发也用独立 venv，保持全局干净

```bash
cd F:\CodeProject\PersonalSpace\monitor

# 开发环境（可选，零依赖所以也可以直接用系统 Python）
python -m venv .venv
.venv\Scripts\activate

# 冒烟验证
python -m disksentry --version
python -m unittest discover -s tests -t .
```

打包环境（PyInstaller）**不要**装进日常环境，`build.bat` 会自动维护 `.venv-build`；
手动方式见 §6。

---

## 4. 调试

### 4.1 日常调试循环（推荐：小目录当监控根）

全盘扫描一轮几分钟，调试时用小目录代替，秒级出结果：

```bash
# 1. 准备一个开发数据目录，把监控根指到小目录
python -m disksentry --data-dir .\data-dev config --set "disks=F:\CodeProject\PersonalSpace\monitor\disksentry"

# 2. 启动（--verbose 开 DEBUG 日志；--no-browser 不弹浏览器）
python -m disksentry --data-dir .\data-dev run --no-browser --verbose

# 3. 改任意被监控目录里的文件制造变更，然后网页上点"立即扫描"或：
python -m disksentry --data-dir .\data-dev scan
```

浏览器开 `http://127.0.0.1:8765`（端口被占会自动向后尝试 10 个，实际端口看控制台输出）。

### 4.2 看日志

- 控制台 + `%LOCALAPPDATA%\DiskSentry\disksentry.log`（或你的 `--data-dir` 下）
- `--verbose` → DEBUG；默认 INFO
- 关键日志行：`扫描完成 X: N 个文件, +a/-b/c 变更`、`扫描根失败`（单根失败不中断其他根）

### 4.3 直接查数据库（验证差分逻辑最快的方式）

用 DB Browser for SQLite，或命令行：

```sql
-- 最近几轮扫描
SELECT id, datetime(started_at,'unixepoch','localtime') t, status,
       file_count, added, removed, changed, error FROM scans ORDER BY id DESC LIMIT 5;
-- 最近变更
SELECT datetime(ts,'unixepoch','localtime') t, kind, path, delta
FROM changes ORDER BY id DESC LIMIT 20;
-- 某时间窗各目录增量（近 24h，直接体会查询语义）
SELECT root, SUM(delta) FROM changes WHERE ts > strftime('%s','now','-24 hours') GROUP BY root;
```

注意 WAL 模式：要看到最新数据请打开连接后执行 `PRAGMA wal_checkpoint;` 或直接用只读方式打开。

### 4.4 前端调试

- 无构建步骤：改 `web/` 下任何文件，浏览器刷新即生效（服务每次请求都重读文件）
- F12 控制台看 fetch 错误；API 可直接 `curl http://127.0.0.1:8765/api/state`
- 深浅色：跟随系统，DevTools 里 `Emulate CSS prefers-color-scheme` 可切换

### 4.5 VS Code 一键调试（可选）

`.vscode/launch.json`：

```json
{
  "version": "0.2.0",
  "configurations": [{
    "name": "DiskSentry run",
    "type": "debugpy",
    "request": "launch",
    "module": "disksentry",
    "args": ["--data-dir", "${workspaceFolder}/data-dev", "--no-browser", "run"],
    "console": "integratedTerminal"
  }]
}
```

### 4.6 常见问题速查

| 现象 | 原因/处理 |
| --- | --- |
| 界面一直"等待首次扫描/暂无数据" | 首轮是基线，无变更数据；等扫完点"立即扫描"出差分 |
| 启动报端口占用 | 自动向后尝试 10 个端口；`--port` 指定；或杀掉旧实例 |
| exe 双击闪退 | 用 `DiskSentry.exe --version` 或控制台版看报错 |
| 某监控根显示不存在 | U 盘拔出/目录改名，日志有 `监控根不存在` |
| exe 被杀软拦截 | 见 §6.5 |

---

## 5. 测试

### 5.1 跑测试

```bash
python -m unittest discover -s tests -t . -v     # 全部 18 个，<1 秒
```

三个文件各自覆盖：

| 文件 | 覆盖 |
| --- | --- |
| `test_store.py` | 基线→差分四类事件的正确性、目录/文件聚合排行、趋势相对窗口起点、保留清理 |
| `test_scanner.py` | 目录树遍历、排除前缀、计数器、文件增长检测 |
| `test_server.py` | 起真实 HTTP 服务：静态资源、state/summary/trend/changes、POST config 落盘、参数校验（含真实扫两轮制造变更） |

### 5.2 写新测试的约定

- 用标准库 `unittest` + `tempfile`，不起外部服务、不碰真实磁盘数据
- 涉及 HTTP 一律绑 `port=0` 拿随机端口（`make_server` 支持）
- 涉及扫描一律用临时目录树，**不要**把真实盘符写进测试

### 5.3 手动验收清单（发版前过一遍）

1. 源码 `run` 启动 → 自动开浏览器，四张盘卡片正常
2. 首轮基线完成 → 点"立即扫描" → 状态变"刚刚"
3. 在监控盘上新建/追加/删除文件 → 再扫 → 排行与明细表出现对应事件
4. 设置对话框取消勾选一块盘 → 保存 → 该卡片变"未监控"、图表少一条线
5. `build.bat` → `dist\DiskSentry.exe` 裸双击启动正常
6. `python -m unittest discover -s tests -t .` 全绿

---

## 6. 打包

### 6.1 先澄清：与 .NET 无关，exe 完全自包含

本项目**没有任何 .NET 依赖**。PyInstaller 的原理是把 CPython 解释器、标准库和
`web/` 资源全部**嵌入单个 exe**，运行时自解压到临时目录执行。因此：

| 目标机器 | 能否运行 dist\DiskSentry.exe |
| --- | --- |
| 无 Python、无 .NET、全新 Windows | ✅ 直接双击即可 |
| 有 Python 的机器 | ✅（与已装 Python 互不干扰） |

真正需要区分的不是"有无 .NET"，而是以下两对选择 ↓

### 6.2 标准打包（onefile，推荐日常用）

```bash
build.bat
```

脚本自动完成：建/复用 `.venv-build` → 装 pyinstaller → 清理旧构建 → 产出
**`dist\DiskSentry.exe`**（约 7MB 单文件，带控制台窗口）。

手动等价操作：

```bash
python -m venv .venv-build
.venv-build\Scripts\pip install -r requirements-build.txt
.venv-build\Scripts\pyinstaller --noconfirm --clean DiskSentry.spec
```

### 6.3 spec 关键点（改动前必读）

- **入口必须是 `run.py`**：直接把 `disksentry/__main__.py` 当入口会因相对导入
  （`from . import ...`）崩溃——历史上踩过，见 `run.py` 注释
- `datas=[('web','web')]`：前端打包进 exe；**改了 `web/` 或 `disksentry/` 都要重新打包**
- `icon='assets/icon.ico'`：exe 图标；想换图标 → 改 `tools/make_icon.py` 的几何/配色
  → `python tools/make_icon.py` 重新生成 → 重跑 `build.bat`（生成用 Pillow，仅设计时依赖）
- `console=True`：带控制台（可看日志、关窗退出）。改成 `False` 得无窗口后台版
  （退出需任务管理器），改完重跑 `build.bat`
- `excludes`：排除 tkinter/unittest 减体积

### 6.4 变体：onedir 版（启动快、改前端不用重打包）

onefile 每次启动要自解压，onedir 则是目录形态、启动快，且 `web/` 落在磁盘上
**可直接改文件热更新前端**：

```bash
.venv-build\Scripts\pyinstaller --noconfirm --onedir --console ^
  --add-data "web;web" --name DiskSentryDir run.py
```

产物在 `dist\DiskSentryDir\DiskSentryDir.exe`（注意 Windows 下 `--add-data` 用分号）。

### 6.5 分发注意

- 未签名 exe 可能被 SmartScreen/杀软提示：onedir + 控制台版误报率最低；
  确认无误后自建排除规则；正式分发可考虑代码签名
- 数据默认在 `%LOCALAPPDATA%\DiskSentry`；U 盘携带用 `--portable`（数据在 exe 旁 `data\`）

---

## 7. 二次开发指引：我想改 X，应该动哪里

| 需求 | 改哪里 | 说明 |
| --- | --- | --- |
| 新增/修改 API | `server.py`（Handler 路由 + `_Ctx`） | 返回一律 `_json()`；读库先 `self._db()`；参数校验抛 `ValueError` 自动变 400 |
| 前端展示新数据 | `web/app.js` 对应 render 函数 | 纯原生 JS + d3，无框架；新增 API 后在 `loadAll` 里加一个 load |
| 调整扫描行为 | `scanner.py::iter_files` | 保持生成器形态；新的跳过规则记得同步 `_excluded` 语义 |
| 差分/存储变更 | `store.py::apply_scan` | 见下方算法说明；改完必跑 `test_store.py` |
| 新告警通道（如 Windows 通知） | `scheduler.py::_check_alert` | 目前只写 alerts 表；可在此处加 toast/邮件 |
| 加配置项 | `config.py::DEFAULTS` → `server.py::_post_config`（白名单校验） → 设置对话框（`web/index.html` + `app.js`） → README 配置表 | 四处要同步 |
| 换 Web 端口/绑定策略 | `server.py::make_server` | 出于安全只绑 127.0.0.1，勿改对外 |
| v2：USN Journal 近实时引擎 | 新模块，产出与 `iter_files` 同形态的变更流 | 架构已预留：采集与存储解耦，`apply_scan` 只认流 |

### 7.1 差分算法（`store.py::apply_scan`）工作原理

每次扫描的清单先进 `TEMP TABLE scan_new`，然后四条集合 SQL 与 `files` 现状做反连接：

1. `files` 中不在 `scan_new` 的 → 写 `delete` 变更并删除
2. `scan_new` 中不在 `files` 的 → 写 `new` 变更并插入
3. 两者都有但 size 不同 → 按方向写 `grow`/`shrink` 变更
4. 更新 `files` 的 size/mtime/last_seen

要点：**不把百万路径拉进 Python 内存**；首轮扫描只建基线不写变更；
单个根失败不影响其他根（`scheduler.py` 的 per-root try/except）。

### 7.2 线程与连接模型（改并发时必读）

- 三个执行环境：主线程（serve_forever）、scheduler 线程（扫描）、HTTP 线程池（API）
- **SQLite 连接不跨线程共享**：每处用 `store.connect()` 新建（WAL 允许读写并发）
- `Config` 自带锁，读快照 `cfg.data`、写 `cfg.update()` 均线程安全
- 扫描必须包裹在 `winutil.background_priority()` 上下文里（低 IO 优先级的承诺）

### 7.3 项目不变量（改代码别破坏）

1. 运行时零第三方依赖（打包体积与免环境的根基）
2. 服务只绑定 127.0.0.1
3. 扫描遍历必须跳过 reparse point；数据库自身目录自动排除在监控外
4. 路径入库前必须过 `_sanitize`（Windows 代理字符文件名会炸 UTF-8 编码，踩过）

---

## 8. 已知边界与 v2 路线

详见 README「已知边界」。摘要：硬链接重复计数、改名/移动识别（NTFS file_id）、
目录聚合仅按直接父目录、统计逻辑大小（st_size）非磁盘占用、USN Journal 增量引擎。
