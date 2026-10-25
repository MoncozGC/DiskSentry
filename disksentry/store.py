"""SQLite 存储：当前文件状态、扫描记录、变更日志、卷用量快照、告警。

差分采用 SQL 集合运算（反连接）在库内完成，避免把百万级路径拉进 Python 内存。
首次扫描只建立基线不记录变更；之后的扫描把新增/删除/增长/缩小写入 changes。
"""
import logging
import shutil
import sqlite3
import time

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL NOT NULL,
  finished_at REAL,
  status TEXT NOT NULL,
  error TEXT,
  file_count INTEGER,
  total_size INTEGER,
  added INTEGER,
  removed INTEGER,
  changed INTEGER
);

CREATE TABLE IF NOT EXISTS roots(
  root TEXT PRIMARY KEY,
  baseline_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS files(
  root TEXT NOT NULL,
  path TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  PRIMARY KEY(root, path)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  root TEXT NOT NULL,
  path TEXT NOT NULL,
  kind TEXT NOT NULL,        -- new / grow / shrink / delete
  old_size INTEGER,
  new_size INTEGER,
  delta INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_ts ON changes(ts);
CREATE INDEX IF NOT EXISTS idx_changes_root_ts ON changes(root, ts);

CREATE TABLE IF NOT EXISTS disk_snapshots(
  root TEXT NOT NULL,
  ts REAL NOT NULL,
  used INTEGER,
  total INTEGER,
  free INTEGER,
  PRIMARY KEY(root, ts)
);

CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  root TEXT,
  message TEXT
);

CREATE TABLE IF NOT EXISTS deletions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  path TEXT NOT NULL,
  size INTEGER,
  source TEXT NOT NULL DEFAULT 'dup'
);
CREATE INDEX IF NOT EXISTS idx_deletions_ts ON deletions(ts);
"""

# 汇总目录排行时最多读取的变更行数（防止超大窗口把内存打爆）
_TOP_DIRS_SCAN_LIMIT = 20000


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- 扫描写入

def begin_scan(conn) -> int:
    cur = conn.execute(
        "INSERT INTO scans(started_at, status) VALUES(?, 'running')", (time.time(),))
    conn.commit()
    return cur.lastrowid


def finish_scan(conn, scan_id: int, status: str, error: str = None,
                file_count: int = 0, total_size: int = 0,
                added: int = 0, removed: int = 0, changed: int = 0):
    conn.execute(
        "UPDATE scans SET finished_at=?, status=?, error=?, file_count=?, total_size=?,"
        " added=?, removed=?, changed=? WHERE id=?",
        (time.time(), status, error, file_count, total_size, added, removed, changed, scan_id))
    conn.commit()


def apply_scan(conn, root: str, ts: float, entries_iter):
    """把一次扫描的完整文件清单合入 files 表，产出 changes。

    entries_iter 产出 (path, size, mtime)。返回统计 dict。
    """
    conn.executescript(
        "CREATE TEMP TABLE IF NOT EXISTS scan_new("
        "path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL) WITHOUT ROWID;"
        "DELETE FROM temp.scan_new;")
    batch = []

    def flush():
        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO temp.scan_new(path,size,mtime) VALUES(?,?,?)", batch)
            batch.clear()

    for entry in entries_iter:
        batch.append(entry)
        if len(batch) >= 10000:
            flush()
    flush()

    file_count, total_size = conn.execute(
        "SELECT COUNT(*), IFNULL(SUM(size),0) FROM temp.scan_new").fetchone()

    # 基线 = "这个根扫过至少一次"（roots 表）。兼容老库：files 已有行同样视为有基线。
    # 不能只用 files 表判断——空目录首扫 files 无行，会导致后续每次都被当作首扫、
    # 变更永远不记录。
    has_baseline = conn.execute(
        "SELECT 1 FROM roots WHERE root=?1 UNION ALL "
        "SELECT 1 FROM files WHERE root=?1 LIMIT 1", (root,)).fetchone() is not None
    conn.execute("INSERT OR REPLACE INTO roots(root, baseline_at) VALUES(?,?)", (root, ts))

    added = removed = changed = 0
    if not has_baseline:
        # 首次扫描：仅建基线，不产生变更记录
        cur = conn.execute(
            "INSERT INTO files(root,path,size,mtime,first_seen,last_seen)"
            " SELECT ?,path,size,mtime,?,? FROM temp.scan_new", (root, ts, ts))
        added = cur.rowcount
    else:
        cur = conn.execute(
            "INSERT INTO changes(ts,root,path,kind,old_size,new_size,delta)"
            " SELECT ?,root,path,'delete',size,NULL,-size FROM files"
            " WHERE root=? AND path NOT IN (SELECT path FROM temp.scan_new)",
            (ts, root))
        removed = cur.rowcount
        conn.execute(
            "DELETE FROM files WHERE root=? AND path NOT IN (SELECT path FROM temp.scan_new)",
            (root,))

        cur = conn.execute(
            "INSERT INTO changes(ts,root,path,kind,old_size,new_size,delta)"
            " SELECT ?,?,path,'new',NULL,size,size FROM temp.scan_new"
            " WHERE path NOT IN (SELECT path FROM files WHERE root=?)",
            (ts, root, root))
        added = cur.rowcount
        conn.execute(
            "INSERT INTO files(root,path,size,mtime,first_seen,last_seen)"
            " SELECT ?,path,size,mtime,?,? FROM temp.scan_new"
            " WHERE path NOT IN (SELECT path FROM files WHERE root=?)",
            (root, ts, ts, root))

        cur = conn.execute(
            "INSERT INTO changes(ts,root,path,kind,old_size,new_size,delta)"
            " SELECT ?,?,s.path,CASE WHEN s.size>f.size THEN 'grow' ELSE 'shrink' END,"
            " f.size,s.size,s.size-f.size"
            " FROM temp.scan_new s JOIN files f ON f.root=? AND f.path=s.path"
            " WHERE s.size<>f.size",
            (ts, root, root))
        changed = cur.rowcount
        conn.execute(
            "UPDATE files SET"
            " size=(SELECT s.size FROM temp.scan_new s WHERE s.path=files.path),"
            " mtime=(SELECT s.mtime FROM temp.scan_new s WHERE s.path=files.path),"
            " last_seen=?"
            " WHERE root=? AND EXISTS("
            "   SELECT 1 FROM temp.scan_new s WHERE s.path=files.path AND s.size<>files.size)",
            (ts, root))

    conn.commit()
    return {"file_count": file_count, "total_size": total_size,
            "added": added, "removed": removed, "changed": changed}


def record_snapshot(conn, root: str, ts: float):
    """记录卷级用量（shutil.disk_usage），供趋势图使用。"""
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return None
    used = usage.total - usage.free
    conn.execute(
        "INSERT OR REPLACE INTO disk_snapshots(root,ts,used,total,free) VALUES(?,?,?,?,?)",
        (root, ts, used, usage.total, usage.free))
    conn.commit()
    return used


def last_snapshot_before(conn, root: str, ts: float):
    row = conn.execute(
        "SELECT ts, used FROM disk_snapshots WHERE root=? AND ts<? ORDER BY ts DESC LIMIT 1",
        (root, ts)).fetchone()
    return (row["ts"], row["used"]) if row else None


def add_alert(conn, root: str, message: str):
    conn.execute("INSERT INTO alerts(ts,root,message) VALUES(?,?,?)",
                 (time.time(), root, message))
    conn.commit()


def prune(conn, retention_days: int):
    """按保留策略清理明细与快照。"""
    cutoff = time.time() - retention_days * 86400
    conn.execute("DELETE FROM changes WHERE ts<?", (cutoff,))
    conn.execute("DELETE FROM disk_snapshots WHERE ts<?", (cutoff,))
    conn.execute("DELETE FROM alerts WHERE ts<?", (cutoff,))
    conn.execute("DELETE FROM scans WHERE started_at<? AND status!='running'", (cutoff,))
    conn.commit()


# ---------------------------------------------------------------- 查询

def last_scan(conn):
    row = conn.execute(
        "SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def window_deltas(conn, since: float):
    """各监控根在时间窗内的净变化字节数。"""
    rows = conn.execute(
        "SELECT root, SUM(delta) AS d FROM changes WHERE ts>=? GROUP BY root", (since,))
    return {r["root"]: r["d"] for r in rows}


def recent_changes(conn, since: float, order: str = "ts", limit: int = 100,
                   offset: int = 0, root: str = None):
    order_sql = "ABS(delta) DESC" if order == "delta" else "ts DESC"
    sql = ("SELECT ts,root,path,kind,old_size,new_size,delta FROM changes WHERE ts>=?")
    params = [since]
    if root:
        sql += " AND root=?"
        params.append(root)
    sql += f" ORDER BY {order_sql} LIMIT ? OFFSET ?"
    params += [int(limit), int(offset)]
    return [dict(r) for r in conn.execute(sql, params)]


def top_dirs(conn, since: float, topn: int = 8, root: str = None):
    """时间窗内增长最多的目录（按文件事件按父目录聚合，仅统计增量>0）。"""
    sql = "SELECT path,root,delta FROM changes WHERE ts>=? AND delta>0"
    params = [since]
    if root:
        sql += " AND root=?"
        params.append(root)
    sql += " ORDER BY delta DESC LIMIT ?"
    params.append(_TOP_DIRS_SCAN_LIMIT)
    dirs = {}
    files = {}
    for r in conn.execute(sql, params):
        path, r_root, delta = r["path"], r["root"], r["delta"]
        f = files.setdefault(path, {"path": path, "root": r_root, "delta": 0})
        f["delta"] += delta
        parent = _parent_dir(path)
        item = dirs.setdefault(parent, {"path": parent, "root": r_root, "delta": 0})
        item["delta"] += delta
    dirs = sorted(dirs.values(), key=lambda x: -x["delta"])[:topn]
    files = sorted(files.values(), key=lambda x: -x["delta"])[:topn]
    return dirs, files


def _parent_dir(path: str) -> str:
    i = path.rfind("\\")
    return path[:i] if i > 0 else path


def trend(conn, since: float):
    """卷用量趋势：每个根一条序列，值为相对窗口起点的净变化字节。"""
    rows = conn.execute(
        "SELECT ts,root,used FROM disk_snapshots WHERE ts>=? ORDER BY ts", (since,))
    series = {}
    for r in rows:
        series.setdefault(r["root"], []).append((r["ts"], r["used"]))
    out = []
    for root, points in series.items():
        base = points[0][1]
        out.append({
            "root": root,
            "points": [{"t": ts * 1000, "v": used - base} for ts, used in points],
        })
    return out


def recent_alerts(conn, limit: int = 10):
    rows = conn.execute(
        "SELECT ts,root,message FROM alerts ORDER BY id DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in rows]


def record_deletions(conn, items):
    """记录删除成功的文件（items: [{path, size?, source?}]），供删除历史查询。"""
    ts = time.time()
    conn.executemany(
        "INSERT INTO deletions(ts,path,size,source) VALUES(?,?,?,?)",
        [(ts, i["path"], i.get("size"), i.get("source", "dup")) for i in items])
    conn.commit()


def recent_deletions(conn, limit: int = 200, offset: int = 0):
    total = conn.execute(
        "SELECT COUNT(*) c, IFNULL(SUM(size),0) s FROM deletions").fetchone()
    rows = conn.execute(
        "SELECT ts,path,size,source FROM deletions ORDER BY id DESC LIMIT ? OFFSET ?",
        (int(limit), int(offset)))
    return {"total": total["c"], "total_size": total["s"],
            "deletions": [dict(r) for r in rows]}


def db_size(db_path) -> int:
    try:
        import os
        return os.path.getsize(db_path)
    except OSError:
        return 0
