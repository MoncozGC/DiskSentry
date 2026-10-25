"""DiskSentry 命令行入口。

用法：
  python -m disksentry run                 # 常驻：调度扫描 + Web 仪表盘（默认）
  python -m disksentry scan [--root PATH]  # 立即执行一轮扫描
  python -m disksentry report --hours 24   # 打印增长报告
  python -m disksentry config [--set k=v]  # 查看/修改配置
全局参数：--data-dir PATH / --portable / --port N / --no-browser
"""
import argparse
import logging
import logging.handlers
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .config import Config
from .paths import default_data_dir
from .scanner import ScanStatus
from .store import db_size

log = logging.getLogger(__name__)


def setup_logging(data_dir: Path, verbose: bool = False):
    data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        data_dir / "disksentry.log", maxBytes=2 * 1024 * 1024,
        backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def cmd_run(args, cfg, data_dir):
    from .scheduler import Scheduler
    from .server import make_server
    from .store import connect

    db_path = data_dir / "data.db"
    connect(db_path).close()  # 预建 schema

    status = ScanStatus()
    scheduler = Scheduler(cfg, db_path, status)
    scheduler.start()

    port = args.port or cfg.data["port"]
    httpd = make_server(cfg, scheduler, status, db_path, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"DiskSentry v{__version__} 已启动: {url}")
    print(f"数据目录: {data_dir}  (数据库 {db_size(db_path) / 1024 / 1024:.1f} MB)")
    print("Ctrl+C 退出")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            log.debug("打开浏览器失败", exc_info=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n正在退出…")
        scheduler.stop()
        httpd.server_close()


def cmd_scan(args, cfg, data_dir):
    from .scheduler import run_scan_cycle
    roots = [args.root] if args.root else None
    if roots:
        from .config import normalize_root
        roots = [normalize_root(r) for r in roots]
    scan_id, totals, err = run_scan_cycle(cfg.data, data_dir / "data.db",
                                          roots_override=roots)
    print(f"扫描完成: {totals['file_count']} 个文件, "
          f"+{totals['added']} 新增 / -{totals['removed']} 删除 / {totals['changed']} 大小变化")
    if err:
        print(f"警告: {err}", file=sys.stderr)
        return 1
    return 0


def cmd_report(args, cfg, data_dir):
    from .report import render_report
    from .store import connect
    conn = connect(data_dir / "data.db")
    try:
        out = render_report(conn, args.hours, top=args.top)
    finally:
        conn.close()
    print(out)
    return 0


def cmd_config(args, cfg, data_dir):
    if args.set:
        import json as _json
        changes = {}
        for kv in args.set:
            if "=" not in kv:
                print(f"格式错误（应为 key=value）: {kv}", file=sys.stderr)
                return 2
            k, v = kv.split("=", 1)
            v = v.strip()
            try:
                if k in ("disks", "exclude"):
                    # 列表型配置：单个路径直接给，或给 JSON 数组
                    changes[k] = _json.loads(v) if v.startswith("[") else [v]
                elif v.lower() in ("true", "false"):
                    changes[k] = v.lower() == "true"
                else:
                    changes[k] = int(v)
            except ValueError:
                try:
                    changes[k] = float(v)
                except ValueError:
                    if k not in ("disks", "exclude"):
                        changes[k] = v
        cfg.update(changes)
        print("已更新")
    import json
    print(json.dumps(cfg.data, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # 公共参数：允许出现在子命令之前或之后
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", help="数据目录（默认 %%LOCALAPPDATA%%\\DiskSentry）")
    common.add_argument("--portable", action="store_true",
                        help="便携模式：数据存放在程序所在目录的 data/ 下")
    common.add_argument("--verbose", action="store_true")

    parser = argparse.ArgumentParser(
        prog="disksentry", description="DiskSentry 磁盘增长监控",
        parents=[common])
    parser.add_argument("--version", action="version", version=__version__)
    # 不带子命令时默认执行 run（双击 exe 的形式），因此主解析器也要接受其参数
    parser.add_argument("--port", type=int, default=None, help="Web 端口（默认取配置）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.set_defaults(cmd="run")
    sub = parser.add_subparsers(dest="cmd")

    # 子命令里的同名参数用 SUPPRESS 缺省：未指定时不覆盖主解析器已解析的值
    p_run = sub.add_parser("run", help="常驻运行：调度扫描 + Web 仪表盘", parents=[common])
    p_run.add_argument("--port", type=int, default=argparse.SUPPRESS,
                       help="Web 端口（默认取配置）")
    p_run.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS,
                       help="启动后不自动打开浏览器")

    p_scan = sub.add_parser("scan", help="立即执行一轮扫描", parents=[common])
    p_scan.add_argument("--root", help="临时指定扫描根（可搭配 --data-dir 测试）")

    p_report = sub.add_parser("report", help="打印增长报告", parents=[common])
    p_report.add_argument("--hours", type=int, default=24)
    p_report.add_argument("--top", type=int, default=15)

    p_cfg = sub.add_parser("config", help="查看/修改配置", parents=[common])
    p_cfg.add_argument("--set", nargs="*", metavar="key=value")

    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir(args.portable)
    setup_logging(data_dir, args.verbose)
    cfg = Config(data_dir / "config.json")

    if args.cmd in (None, "run"):
        cmd_run(args, cfg, data_dir)
        return 0
    if args.cmd == "scan":
        return cmd_scan(args, cfg, data_dir)
    if args.cmd == "report":
        return cmd_report(args, cfg, data_dir)
    if args.cmd == "config":
        return cmd_config(args, cfg, data_dir)
    return 2


if __name__ == "__main__":
    sys.exit(main())
