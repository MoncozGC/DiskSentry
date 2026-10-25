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

**性能参考（量级，以实测为准）**：扫描用 `os.scandir`（列目录自带元数据，无需逐文件
stat），SSD 全盘（百万级文件）典型分钟级；差分在 SQLite 库内集合运算完成，常态秒级；
数据库体积约为每文件数百字节（files+changes 两表）；重复文件扫描的耗时集中在全量
MD5（受磁盘顺序读速限制），大小分桶已排除绝大多数文件。
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
│   ├── duplicates.py       重复文件引擎：MD5 分桶比对/同名版本/回收站删除/资源管理器定位
│   ├── server.py           本地 HTTP 服务：静态页 + JSON API（只绑 127.0.0.1）
│   └── report.py           终端文字报告（report 命令）
│
├── web/                    前端（无构建步骤，改完刷新即生效）
│   ├── index.html          页面骨架（顶栏/卡片/图表/排行/明细/设置对话框）
│   ├── app.js              ★ 全部前端逻辑：API 拉取、渲染、d3 趋势图、交互
│   ├── style.css           样式，跟随系统深浅色（prefers-color-scheme）
│   ├── favicon.svg/png     网页图标（由 tools/make_icon.py 生成）
│   ├── dup.html / dup.js   重复文件清理页（分组列表/预选/批量删除）
│   └── vendor/d3.min.js    本地 d3 7.9.0（离线可用，勿升级路径写法）
│
├── assets/                 图标资源（icon.ico 供打包，icon.svg 矢量原稿）
├── tools/
│   └── make_icon.py        图标生成脚本（Pillow，一次性资产生成，非运行时依赖）
│
├── tests/                  单元测试（unittest，见 §5）
│   ├── test_store.py       差分算法 / 查询 / 清理
│   ├── test_scanner.py     遍历 / 排除 / 计数
│   ├── test_server.py      API 全链路（含真实扫描两轮制造差分）
│   ├── test_paths.py       数据目录规则（exe 同级 / 不可写降级）
│   ├── test_cli.py         CLI 参数位置回归（argparse 子解析器覆盖坑）
│   └── test_duplicates.py  重复文件分组 / 回收站删除 / 删除记录 API
│
├── .venv-build/            打包专用虚拟环境（git 忽略，build.bat 自动创建）
├── build/ dist/            PyInstaller 中间产物与 exe（git 忽略）
└── data/ data-dev/         便携模式/开发调试的数据目录（git 忽略）
```

★ 标记的是改动频率最高、最值得先读的文件。

### 2.1 运行时数据目录

- **exe 版**：默认在 **exe 同级 `data\`**（开箱便携；不可写时自动降级到 `%LOCALAPPDATA%\DiskSentry`）
- **源码运行**：默认 `%LOCALAPPDATA%\DiskSentry\`；`--portable` 用项目目录 `data\`；`--data-dir` 显式指定

| 文件 | 内容 |
| --- | --- |
| `config.json` | 配置（见 README §配置） |
| `data.db` | SQLite 数据库（WAL 模式；另有 `data.db-wal/-shm` 临时文件属正常） |
| `disksentry.log` | 运行日志，2MB×2 自动轮转 |

### 2.2 数据库表结构（`store.py::SCHEMA`，SQLite · WAL 模式）

通用约定：**时间戳一律为 Unix 秒（REAL）**；路径为净化后的绝对路径（含非法代理字符时以 `?` 替换）；
数据库开启 WAL（`data.db-wal/-shm` 为正常伴生文件），读写各用独立连接。

#### files — 当前文件状态快照（每轮扫描后即为最新全量）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| root | TEXT | 联合主键 | 监控根（规范化，盘符带尾部 `\`，如 `C:\`） |
| path | TEXT | 联合主键 | 文件绝对路径（`root` 之下） |
| size | INTEGER | NOT NULL | 文件逻辑大小（字节，st_size） |
| mtime | REAL | NOT NULL | 文件修改时间；仅文件大小变化时更新 |
| first_seen | REAL | NOT NULL | 首次出现在扫描中的时间 |
| last_seen | REAL | NOT NULL | 最近一次出现/变更时间；**不做每轮全量刷新**（百万行 UPDATE 代价过高），仅新增与变更时写 |

`WITHOUT ROWID` 表。单根"是否已有基线"的判定不在本表（空目录首扫本表无行），见 `roots`。

#### changes — ★ 变更日志（一切时间窗查询的事实来源）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK 自增 | 事件顺序 |
| ts | REAL | NOT NULL | 事件时间 = 发现该事件的扫描时间（精度 ±1 个扫描间隔） |
| root | TEXT | NOT NULL | 所在监控根 |
| path | TEXT | NOT NULL | 文件绝对路径 |
| kind | TEXT | NOT NULL | 事件类型：`new` 新建 / `grow` 增长 / `shrink` 缩小 / `delete` 删除 |
| old_size | INTEGER | 可空 | 变更前大小；`new` 事件为 NULL |
| new_size | INTEGER | 可空 | 变更后大小；`delete` 事件为 NULL |
| delta | INTEGER | NOT NULL | 净变化字节（删除为负） |

索引：`idx_changes_ts(ts)`（时间窗查询）、`idx_changes_root_ts(root, ts)`（按盘筛选）。
首次基线扫描不产生记录；目录排行/文件排行按本表聚合（`store.top_dirs`）。

#### roots — 基线标记

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| root | TEXT | PK | 监控根 |
| baseline_at | REAL | NOT NULL | 首次扫描时间 |

"该根至少扫过一次"即视为已建基线（即使当时目录为空）；老库兼容：`files` 有行同样视为有基线。

#### scans — 每轮扫描元信息

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK 自增 | 扫描轮次 |
| started_at | REAL | NOT NULL | 开始时间 |
| finished_at | REAL | 可空 | 结束时间；`running` 状态为 NULL |
| status | TEXT | NOT NULL | `running` / `ok` / `partial`（部分根失败）/ `error` |
| error | TEXT | 可空 | 失败详情（各根错误以 `;` 连接） |
| file_count | INTEGER | | 本轮所有根文件总数 |
| total_size | INTEGER | | 本轮所有根总字节数 |
| added / removed / changed | INTEGER | | 新增/删除/大小变化的文件数（基线轮 added=全部） |

#### disk_snapshots — 卷级用量快照（趋势图数据源）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| root | TEXT | 联合主键 | 监控根 |
| ts | REAL | 联合主键 | 快照时间（每根每轮一条，扫描前记录） |
| used | INTEGER | 可空 | 已用字节（total - free，`shutil.disk_usage`） |
| total | INTEGER | 可空 | 总容量 |
| free | INTEGER | 可空 | 剩余空间 |

#### alerts — 增长告警

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK 自增 | |
| ts | REAL | NOT NULL | 告警时间 |
| root | TEXT | 可空 | 触发的监控根 |
| message | TEXT | | 文案（如 `C:\ 增速 6.2 GB/小时，超过阈值`） |

#### deletions — 删除记录（重复文件清理的操作审计）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK 自增 | |
| ts | REAL | NOT NULL | 删除时间 |
| path | TEXT | NOT NULL | 被删文件的完整路径（已进回收站） |
| size | INTEGER | 可空 | 删除时的文件大小（缓存结果缺失时为 NULL） |
| source | TEXT | NOT NULL | 删除来源，当前仅 `dup`；预留扩展 |

索引：`idx_deletions_ts(ts)`。**不随保留策略清理**（属操作审计，量级小）。

#### meta — 预留键值表

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| key | TEXT | PK | |
| value | TEXT | | 预留（如未来 schema 版本号） |

**保留策略**（`store.prune`，每日执行）：`changes`、`disk_snapshots`、`alerts`、
已结束的 `scans` 按 `retention_days`（默认 90 天）清理；`files`/`roots` 为当前态、
`deletions` 为审计记录，均不清理。

手工查看示例见 §4.3。

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
python -m unittest discover -s tests -t . -v     # 全部 34 个，<2 秒
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

### 5.4 发版流程

1. 测试全绿 + §5.3 手动验收清单过一遍
2. 更新 `disksentry/__init__.py` 版本号与 `CHANGELOG.md`，重新打包并冒烟
   （`dist\DiskSentry.exe --version`）
3. 提交并打 tag：`git tag -a v1.x.0 -m "DiskSentry v1.x.0" && git push --tags`
4. GitHub Releases：新建 Release 关联 tag，正文贴 CHANGELOG 对应段落，
   **手动上传 `dist\DiskSentry.exe` 作为附件**（exe 不入库）

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
| 改重复文件功能 | `duplicates.py` + `web/dup.js` | 分组规则在 `scan_directory`；删除走回收站（SHFileOperation） |
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

## 8. Web API 参考（`server.py`，仅绑定 127.0.0.1）

通用约定：

- 请求/响应均为 JSON（UTF-8）；GET 带 query 参数，POST 为 JSON body
- 错误统一返回 `{"error": "描述"}`，状态码：400 参数错误 / 404 不存在 / 409 冲突 / 413 请求体过大 / 500 内部错误
- POST body 上限 1MB；仅 `/api/dup/delete` 放宽到 5MB（数千长路径场景）
- 时间戳字段：`ts` 秒（Unix）；趋势图 `t` 为毫秒

### 8.1 GET /api/state — 全局状态

无参数。仪表盘每次打开与 30 秒轮询调用。

| 返回字段 | 类型 | 说明 |
| --- | --- | --- |
| version | string | 程序版本 |
| available | array | 固定盘 ∪ 监控根（去重）；元素 `{root, label, total, used, free}`；label 为卷标可空串；目录根的容量取所在卷 |
| config | object | 当前完整配置（同 README §配置） |
| scanning | object | `{running, current_root, entries, started_at, last_finished, last_error}` 增长监控扫描进度 |
| last_scan | object\|null | 最近一轮扫描 `{id, started_at, finished_at, status(ok/partial/error/running), error, file_count, total_size, added, removed, changed}` |
| alerts | array | 最近 10 条增长告警 `{ts, root, message}` |
| db_size | int | 数据库文件字节数 |

### 8.2 GET /api/summary?window=24&topn=8&root=C:%5C — 时间窗汇总

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| window | int | 24 | 时间窗（小时），1~17520，非法 → 400 |
| topn | int | 8 | 排行条数，1~50（超出收敛到 50，不报错） |
| root | string | 全部 | 仅统计该监控根（须 URL 编码，如 `C:\` → `C:%5C`） |

| 返回字段 | 类型 | 说明 |
| --- | --- | --- |
| deltas | object | 各根净变化字节 `{root: delta}`，可为负 |
| top_dirs | array | 增长最快目录 `{path, root, delta}`（仅增量>0，按父目录聚合） |
| top_files | array | 增长最快文件 `{path, root, delta}` |
| changes | array | 按时间倒序前 50 条变更，结构见 §8.4 |

### 8.3 GET /api/trend?window=24 — 各盘净增长趋势

`window` 同 §8.2。返回：

```json
{ "window_hours": 24,
  "series": [ { "root": "C:\\", "points": [ { "t": 1761400000000, "v": 5368709120 } ] } ] }
```

`points.v` = 相对窗口起点的卷用量净变化（字节，来自 disk_snapshots），`t` 毫秒时间戳。

### 8.4 GET /api/changes?window=24&order=ts&limit=100&offset=0&root=… — 变更明细

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| order | string | ts | `ts` 时间倒序 / `delta` 按 abs(delta) 倒序；其他 → 400 |
| limit | int | 100 | ≤500 |
| offset | int | 0 | 分页偏移 |

变更对象结构（下同）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| ts | float | 事件时间（=发现它的扫描时间，精度 ±1 个扫描间隔） |
| root / path | string | 监控根 / 文件绝对路径 |
| kind | string | `new` 新建 / `grow` 增长 / `shrink` 缩小 / `delete` 删除 |
| old_size / new_size | int\|null | 变更前后大小；new 事件 old_size 为 null，delete 事件 new_size 为 null |
| delta | int | 净变化字节（删除为负） |

返回 `{"window_hours": int, "changes": [...]}`。

### 8.5 POST /api/config — 修改配置

body 为配置对象的部分字段（白名单同 README §配置 的键；`disks`/`exclude` 为数组）。
原子写回 `config.json`，调度线程下一循环生效。返回 `{"ok": true, "config": {完整配置}}`。

### 8.6 POST /api/scan — 触发立即扫描

无 body。返回 `202 {"ok": true}`；已在扫描中 → `409 {"ok": false, "detail": "扫描进行中"}`。

### 8.7 POST /api/dup/scan — 启动重复文件扫描

body：`{"root": "D:\\Downloads"}`（必填，须存在，否则 400）。
后台线程执行（大小分桶 → 部分 MD5 → 全量 MD5），返回 `202 {"ok": true}`；
已有扫描进行中 → `409 {"ok": false, "detail": "已有扫描进行中"}`。

### 8.8 GET /api/dup/status — 重复扫描进度

返回 `{"running": bool, "root": string|null, "error": string|null, "has_result": bool,
"progress": {"done": int, "total": int, "phase": "idle|hash|done|error"}}`。

### 8.9 GET /api/dup/result — 重复扫描结果

尚未完成 → 404；扫描失败 → 500。成功返回：

```json
{
  "root": "D:\\Downloads", "file_count": 12345,
  "duplicate_groups": [
    { "size": 1048576, "md5": "…", "keep": 0,
      "files": [ { "path": "D:\\a.docx", "mtime": 1761400000.0 } ] } ],
  "similar_groups": [
    { "name": "优化项目文档", "keep": 1,
      "files": [ { "path": "…(1).docx", "size": 512000, "mtime": 1761400000.0, "md5": "…" } ] } ]
}
```

| 字段 | 说明 |
| --- | --- |
| duplicate_groups | MD5 完全一致的组，按可释放空间（size×(n-1)）降序；files 排序：无 `(1)/副本` 后缀在前、同类时间倒序；`keep` 为保留项索引（恒 0） |
| similar_groups | 名字归一化相同、内容不同的版本组；files 按 mtime 倒序（最新在前）；`keep` 指向无噪音后缀的最新文件（全组都有后缀时为 0） |
| name | 归一化后的名字（已剔除 (1)/副本/数字等噪音） |

### 8.10 POST /api/dup/delete — 批量删除到回收站

body：`{"paths": ["绝对路径", …]}`（必填非空数组、每项绝对路径、≤5000，否则 400）。
逐个移入回收站（SHFileOperationW，可撤销）；成功项写入 `deletions` 表并从缓存结果移除。

返回：`{"deleted": ["路径"], "failed": [{"path": "路径", "error": "原因"}]}`。

### 8.11 POST /api/dup/reveal — 资源管理器定位

body：`{"path": "绝对路径"}`。在资源管理器中打开并选中该文件，
返回 `{"ok": true}`；文件不存在 → 404。

### 8.12 GET /api/deletions?limit=200&offset=0 — 删除记录

`limit` ≤1000。返回：

```json
{ "total": 12, "total_size": 5368709120,
  "deletions": [ { "ts": 1761400000.0, "path": "D:\\x (1).docx", "size": 512000, "source": "dup" } ] }
```

按 id 倒序；`source` 为删除来源（当前仅 `dup`，预留扩展）。

### 8.13 静态资源

`GET /`（仪表盘）、`/dup.html`（重复文件清理）、`/app.js`、`/dup.js`、`/style.css`、
`/favicon.svg`、`/favicon.png`、`/vendor/d3.min.js`。

---

## 9. 已知边界与 v2 路线

详见 README「已知边界」。摘要：硬链接重复计数、改名/移动识别（NTFS file_id）、
目录聚合仅按直接父目录、统计逻辑大小（st_size）非磁盘占用、USN Journal 增量引擎。
