import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from disksentry.config import Config
from disksentry.scheduler import run_scan_cycle
from disksentry.scanner import ScanStatus
from disksentry.server import make_server
from disksentry.store import connect


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.tmp.name) / "data"     # 与被监控目录分离，避免被自动排除
        cls.data_dir.mkdir()
        cls.tree = Path(cls.tmp.name) / "tree"
        (cls.tree / "sub").mkdir(parents=True)
        (cls.tree / "a.txt").write_bytes(b"a" * 100)
        (cls.tree / "sub" / "b.bin").write_bytes(b"b" * 500)

        cls.cfg = Config(cls.data_dir / "config.json")
        cls.cfg.update({"disks": [str(cls.tree)], "interval_minutes": 60})
        cls.db = cls.data_dir / "data.db"
        run_scan_cycle(cls.cfg.data, cls.db)

        # 制造变更：增长 + 新建 + 删除，再扫一轮产生 changes
        (cls.tree / "a.txt").write_bytes(b"a" * 900)
        (cls.tree / "new.log").write_bytes(b"n" * 300)
        (cls.tree / "sub" / "b.bin").unlink()
        run_scan_cycle(cls.cfg.data, cls.db)

        cls.httpd = make_server(cls.cfg, None, ScanStatus(), cls.db, 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, expect=200):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}")
        try:
            self.assertEqual(resp.status, expect)
            return resp
        except Exception:
            resp.close()
            raise

    def get_json(self, path):
        with self.get(path) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, path, obj, expect=200):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, expect)
            return json.loads(resp.read().decode("utf-8"))

    def test_index_served(self):
        with self.get("/") as resp:
            body = resp.read().decode("utf-8")
            self.assertIn("DiskSentry", body)
            self.assertIn("text/html", resp.headers["Content-Type"])

    def test_static_assets(self):
        with self.get("/app.js") as resp:
            self.assertIn("javascript", resp.headers["Content-Type"])
        with self.get("/vendor/d3.min.js") as resp:
            self.assertGreater(int(resp.headers["Content-Length"]), 100000)
        with self.get("/style.css") as resp:
            self.assertIn("css", resp.headers["Content-Type"])
        with self.get("/favicon.svg") as resp:
            self.assertIn("svg", resp.headers["Content-Type"])
        with self.get("/favicon.png") as resp:
            self.assertGreater(int(resp.headers["Content-Length"]), 100)

    def test_state_contains_monitored_root(self):
        state = self.get_json("/api/state")
        roots = [d["root"] for d in state["available"]]
        self.assertIn(str(self.tree), roots)
        self.assertIn(str(self.tree), state["config"]["disks"])
        self.assertEqual(state["last_scan"]["status"], "ok")
        self.assertEqual(state["last_scan"]["changed"], 1)

    def test_summary_has_changes(self):
        summary = self.get_json("/api/summary?window=24")
        self.assertIn(str(self.tree), summary["deltas"])
        # a.txt +800, new.log +300, b.bin -500 => 净 +600
        self.assertEqual(summary["deltas"][str(self.tree)], 600)
        paths = [c["path"] for c in summary["changes"]]
        self.assertTrue(any(p.endswith("a.txt") for p in paths))
        self.assertTrue(any(p.endswith("new.log") for p in paths))

    def test_trend_series(self):
        trend = self.get_json("/api/trend?window=24")
        self.assertTrue(any(s["root"] == str(self.tree) for s in trend["series"]))

    def test_changes_endpoint_order(self):
        data = self.get_json("/api/changes?window=24&order=delta&limit=10")
        deltas = [abs(c["delta"]) for c in data["changes"]]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_summary_topn_param(self):
        data = self.get_json("/api/summary?window=24&topn=2")
        self.assertLessEqual(len(data["top_dirs"]), 2)
        self.assertLessEqual(len(data["top_files"]), 2)
        # 超范围收敛到 50 而不是报错；非数字才 400
        data = self.get_json("/api/summary?window=24&topn=99")
        self.assertLessEqual(len(data["top_dirs"]), 50)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_json("/api/summary?window=24&topn=abc")
        self.assertEqual(ctx.exception.code, 400)

    def test_post_config_persists(self):
        merged = self.post_json("/api/config", {"interval_minutes": 30,
                                                "alert_threshold_gb_per_hour": 2.5})
        self.assertEqual(merged["config"]["interval_minutes"], 30)
        cfg2 = Config(self.data_dir / "config.json")
        self.assertEqual(cfg2.data["interval_minutes"], 30)
        self.assertEqual(cfg2.data["alert_threshold_gb_per_hour"], 2.5)

    def test_post_scan_without_scheduler(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post_json("/api/scan", {}, expect=400)
        self.assertEqual(ctx.exception.code, 400)

    def test_bad_window_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_json("/api/summary?window=abc")
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
