import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from disksentry import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_baseline_then_diff(self):
        root = "T:\\"
        r1 = store.apply_scan(self.conn, root, 1000.0, [
            (r"T:\d1\a.txt", 100, 1.0),
            (r"T:\d1\b.txt", 50, 2.0),
            (r"T:\d2\c.txt", 700, 3.0),
        ])
        # 首次扫描建基线：不产生变更记录
        self.assertEqual(r1["file_count"], 3)
        self.assertEqual(r1["added"], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM changes").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM files").fetchone()["c"], 3)

        r2 = store.apply_scan(self.conn, root, 2000.0, [
            (r"T:\d1\a.txt", 150, 1.5),   # grow +50
            (r"T:\d3\e.txt", 30, 4.0),    # new +30
            # c.txt 消失 -> delete -700; b.txt 消失 -> delete -50
        ])
        self.assertEqual(r2["added"], 1)
        self.assertEqual(r2["removed"], 2)
        self.assertEqual(r2["changed"], 1)

        kinds = {row["path"]: (row["kind"], row["delta"]) for row in
                 self.conn.execute("SELECT path,kind,delta FROM changes")}
        self.assertEqual(kinds[r"T:\d1\a.txt"], ("grow", 50))
        self.assertEqual(kinds[r"T:\d3\e.txt"], ("new", 30))
        self.assertEqual(kinds[r"T:\d2\c.txt"], ("delete", -700))
        self.assertEqual(kinds[r"T:\d1\b.txt"], ("delete", -50))

        files = {row["path"]: row["size"] for row in
                 self.conn.execute("SELECT path,size FROM files")}
        self.assertEqual(files, {r"T:\d1\a.txt": 150, r"T:\d3\e.txt": 30})

        deltas = store.window_deltas(self.conn, 1500.0)
        self.assertEqual(deltas[root], 50 + 30 - 700 - 50)

    def test_shrink_recorded(self):
        store.apply_scan(self.conn, "T:\\", 1000.0, [(r"T:\x.bin", 900, 1.0)])
        store.apply_scan(self.conn, "T:\\", 2000.0, [(r"T:\x.bin", 400, 1.5)])
        row = self.conn.execute("SELECT kind,delta FROM changes").fetchone()
        self.assertEqual(row["kind"], "shrink")
        self.assertEqual(row["delta"], -500)

    def test_top_dirs_and_recent_changes(self):
        ts = 1000.0
        rows = [
            (r"T:\proj\logs\a.log", 500),
            (r"T:\proj\logs\b.log", 300),
            (r"T:\proj\logs\a.log", 200),
            (r"T:\other\c.iso", 400),
        ]
        conn = self.conn
        conn.executemany(
            "INSERT INTO changes(ts,root,path,kind,old_size,new_size,delta)"
            " VALUES(?,?,?,?,?,?,?)",
            [(ts, "T:\\", p, "grow", 0, d, d) for p, d in rows])
        conn.commit()
        dirs, files = store.top_dirs(conn, ts - 1, topn=2)
        # a.log 累计 +700，目录聚合 logs=1000 > other=400
        self.assertEqual(dirs[0]["path"], r"T:\proj\logs")
        self.assertEqual(dirs[0]["delta"], 1000)
        self.assertEqual(files[0]["path"], r"T:\proj\logs\a.log")
        self.assertEqual(files[0]["delta"], 700)

        recent = store.recent_changes(conn, ts - 1, order="delta", limit=2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["delta"], 500)

    def test_trend_relative_to_window_start(self):
        conn = self.conn
        for ts, used in [(1000, 100), (2000, 130), (3000, 90)]:
            conn.execute(
                "INSERT INTO disk_snapshots(root,ts,used,total,free) VALUES(?,?,?,?,?)",
                ("T:\\", ts, used, 500, 400))
        conn.commit()
        series = store.trend(conn, 900)
        self.assertEqual(len(series), 1)
        self.assertEqual([p["v"] for p in series[0]["points"]], [0, 30, -10])

    def test_empty_root_baseline_then_changes(self):
        """空目录首扫也应建立基线：之后的新文件必须记为变更。

        曾有 bug：基线判定只看 files 表是否有行，空目录首扫后 files 为空，
        导致该根每次扫描都被当作首扫，变更永远不记录。
        """
        r1 = store.apply_scan(self.conn, "E:\\", 1000.0, [])
        self.assertEqual(r1["added"], 0)
        r2 = store.apply_scan(self.conn, "E:\\", 2000.0, [(r"E:\x.txt", 10, 1.0)])
        self.assertEqual(r2["added"], 1)
        rows = self.conn.execute("SELECT kind,delta FROM changes").fetchall()
        self.assertEqual([r["kind"] for r in rows], ["new"])
        self.assertEqual(rows[0]["delta"], 10)

    def test_prune(self):
        conn = self.conn
        conn.execute("INSERT INTO changes(ts,root,path,kind,old_size,new_size,delta)"
                     " VALUES(1,'T:\\','p','new',0,1,1)")
        conn.execute("INSERT INTO changes(ts,root,path,kind,old_size,new_size,delta)"
                     " VALUES(9999999999,'T:\\','p2','new',0,1,1)")
        conn.commit()
        store.prune(conn, 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM changes").fetchone()["c"], 1)


if __name__ == "__main__":
    unittest.main()
