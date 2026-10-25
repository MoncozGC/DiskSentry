"""调度器：按配置间隔循环扫描全部监控根，评估增长告警，执行保留策略。"""
import logging
import threading
import time
from pathlib import Path

from . import scanner, store, winutil

log = logging.getLogger(__name__)

GB = 1024 ** 3


def run_scan_cycle(cfg_data: dict, db_path, status: scanner.ScanStatus = None,
                   roots_override=None):
    """执行一轮完整扫描（所有根）。供调度线程与 CLI 单次扫描共用。

    单个根失败不影响其他根；返回 (scan_id, totals, error)。
    """
    status = status or scanner.ScanStatus()
    status.begin()
    roots = roots_override if roots_override is not None else cfg_data["disks"]
    # 数据库所在目录自动排除，避免监控到自身增长
    excludes = list(cfg_data.get("exclude") or [])
    db_parent = str(Path(db_path).resolve().parent)
    if db_parent not in excludes:
        excludes.append(db_parent)

    conn = store.connect(db_path)
    scan_id = None
    totals = {"file_count": 0, "total_size": 0, "added": 0, "removed": 0, "changed": 0}
    root_errors = []
    ok_roots = 0
    try:
        with winutil.background_priority():
            scan_id = store.begin_scan(conn)
            for root in roots:
                if not winutil.root_exists(root):
                    log.warning("监控根不存在，跳过: %s", root)
                    root_errors.append(f"{root}: 不存在")
                    continue
                try:
                    ts = time.time()
                    store.record_snapshot(conn, root, ts)
                    prev = store.last_snapshot_before(conn, root, ts)
                    result = scanner.scan_root(conn, root, ts,
                                               excludes=excludes, status=status)
                    for k in totals:
                        totals[k] += result.get(k, 0)
                    ok_roots += 1
                    log.info("扫描完成 %s: %d 个文件, +%d/-%d/%d 变更",
                             root, result["file_count"], result["added"],
                             result["removed"], result["changed"])
                    _check_alert(conn, cfg_data, root, ts, prev)
                except Exception as e:  # noqa: BLE001 - 单根失败不中断其他根
                    log.exception("扫描根失败: %s", root)
                    root_errors.append(f"{root}: {type(e).__name__}: {e}")
            if not roots:
                status_ = "error"
                err = "没有配置任何监控根"
            elif root_errors and ok_roots:
                status_ = "partial"
                err = "; ".join(root_errors)
            elif root_errors:
                status_ = "error"
                err = "; ".join(root_errors)
            else:
                status_ = "ok"
                err = None
            store.finish_scan(conn, scan_id, status_, error=err, **totals)
        status.finish(error=err if root_errors else None)
        return scan_id, totals, err
    except Exception as e:  # noqa: BLE001 - 兜底：任何意外都要记录并暴露给 UI
        log.exception("扫描循环失败")
        err = f"{type(e).__name__}: {e}"
        if scan_id is not None:
            try:
                store.finish_scan(conn, scan_id, "error", error=err, **totals)
            except Exception:
                log.exception("记录扫描失败状态时出错")
        status.finish(error=err)
        return scan_id, totals, err
    finally:
        conn.close()


def _check_alert(conn, cfg_data, root, ts, prev):
    """对比上一张卷快照，增速超阈值则记一条告警。"""
    if not prev:
        return
    prev_ts, prev_used = prev
    hours = (ts - prev_ts) / 3600.0
    if hours < 1.0 / 60:
        return
    cur = conn.execute(
        "SELECT used FROM disk_snapshots WHERE root=? AND ts=?", (root, ts)).fetchone()
    if not cur or cur["used"] is None:
        return
    rate_gb_h = (cur["used"] - prev_used) / GB / hours
    if rate_gb_h > float(cfg_data.get("alert_threshold_gb_per_hour", 5.0)):
        msg = f"{root} 增速 {rate_gb_h:.1f} GB/小时，超过阈值"
        store.add_alert(conn, root, msg)
        log.warning("告警: %s", msg)


class Scheduler(threading.Thread):
    """后台循环：到期扫描；支持外部触发立即扫描与空闲检测。"""

    def __init__(self, cfg, db_path, status: scanner.ScanStatus):
        super().__init__(daemon=True, name="disksentry-scheduler")
        self.cfg = cfg
        self.db_path = db_path
        self.status = status
        self._stop = threading.Event()
        self._force = threading.Event()
        self._last_prune_day = None

    def stop(self):
        self._stop.set()

    def request_scan(self):
        self._force.set()

    def _due(self) -> bool:
        conn = store.connect(self.db_path)
        try:
            last = store.last_scan(conn)
        finally:
            conn.close()
        if not last or not last.get("finished_at"):
            return True
        return time.time() - last["finished_at"] >= self.cfg.data["interval_minutes"] * 60

    def run(self):
        while not self._stop.is_set():
            try:
                cfg = self.cfg.data
                due = self._due() or self._force.is_set()
                idle_ok = True
                if cfg.get("only_when_idle"):
                    idle_ok = winutil.idle_seconds() >= cfg.get("idle_wait_minutes", 10) * 60
                if due and idle_ok:
                    self._force.clear()
                    run_scan_cycle(cfg, self.db_path, self.status)
                    self._daily_prune(cfg)
            except Exception:
                log.exception("调度循环异常")
            self._stop.wait(1.0)

    def _daily_prune(self, cfg):
        today = time.strftime("%Y-%m-%d")
        if self._last_prune_day == today:
            return
        self._last_prune_day = today
        conn = store.connect(self.db_path)
        try:
            store.prune(conn, int(cfg.get("retention_days", 90)))
            log.info("已按保留策略清理历史数据")
        finally:
            conn.close()
