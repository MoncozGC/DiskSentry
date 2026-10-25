import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from disksentry import duplicates
from disksentry.config import Config
from disksentry.scanner import ScanStatus
from disksentry.server import make_server

X1 = b"x" * (100 * 1024) + b"1"
X2 = b"x" * (100 * 1024) + b"1"     # 与 X1 完全一致
Y = b"y" * (100 * 1024) + b"other"  # 大小相近但内容不同


def make_tree(base: Path):
    (base / "sub").mkdir(parents=True)
    (base / "a1.bin").write_bytes(X1)
    (base / "sub" / "a2.bin").write_bytes(X2)
    (base / "报告.txt").write_text("旧版内容", encoding="utf-8")
    (base / "报告 (1).txt").write_text("新版内容", encoding="utf-8")
    (base / "unique.bin").write_bytes(b"u" * 1024)
    (base / "empty.bin").write_bytes(b"")
    # 让同名版本文件 mtime 错开
    old = (base / "报告.txt")
    t = time.time() - 3600
    import os
    os.utime(old, (t, t))


class DuplicatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        make_tree(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_norm_stem(self):
        self.assertEqual(duplicates.norm_stem("照片 (1).jpg"), "照片")
        self.assertEqual(duplicates.norm_stem("file - 副本.docx"), "file")
        self.assertEqual(duplicates.norm_stem("report_final.PDF"), "report")
        self.assertEqual(duplicates.norm_stem("DATA.bin"), "data")

    def test_scan_groups(self):
        result = duplicates.scan_directory(str(self.base))
        dup_files = {f["path"] for g in result["duplicate_groups"] for f in g["files"]}
        self.assertIn(str(self.base / "a1.bin"), dup_files)
        self.assertIn(str(self.base / "sub" / "a2.bin"), dup_files)

        sim = [g for g in result["similar_groups"] if g["name"] == "报告"]
        self.assertEqual(len(sim), 1)
        g = sim[0]
        files = g["files"]
        self.assertEqual(len(files), 2)
        # 展示顺序按时间倒序：新文件在前
        self.assertTrue(files[0]["mtime"] > files[1]["mtime"])
        self.assertEqual(files[0]["path"], str(self.base / "报告 (1).txt"))
        # 保留项优先选无 (1)/副本 后缀的原始文件，而不是最新的带后缀文件
        self.assertEqual(files[g["keep"]]["path"], str(self.base / "报告.txt"))

    def test_keeper_prefers_clean_name(self):
        """完全重复组：带 (1) 后缀的更新副本不应作为保留项。"""
        clean = self.base / "优化项目文档.docx"
        noisy = self.base / "优化项目文档(1).docx"
        clean.write_bytes(b"same-content")
        noisy.write_bytes(b"same-content")
        import os
        t_new = time.time() + 3600  # 带后缀的反而更新
        os.utime(noisy, (t_new, t_new))
        result = duplicates.scan_directory(str(self.base))
        group = next(g for g in result["duplicate_groups"]
                     if any("优化项目文档" in f["path"] for f in g["files"]))
        self.assertEqual(group["files"][group["keep"]]["path"], str(clean))
        self.assertEqual(group["files"][0]["path"], str(clean))

    def test_identical_content_not_in_similar(self):
        """内容完全一致的同名噪音文件不应出现在"同名版本"里（已在完全重复中）。"""
        (self.base / "copy (1).txt").write_bytes(b"same")
        (self.base / "copy (2).txt").write_bytes(b"same")
        result = duplicates.scan_directory(str(self.base))
        self.assertFalse(any(g["name"] == "copy" for g in result["similar_groups"]))
        dup_paths = {f["path"] for g in result["duplicate_groups"] for f in g["files"]}
        self.assertIn(str(self.base / "copy (1).txt"), dup_paths)

    def test_recycle_files_removes(self):
        target = self.base / "unique.bin"
        result = duplicates.recycle_files([str(target)])
        self.assertEqual(result["deleted"], [str(target)])
        self.assertFalse(target.exists())


class DupApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.tmp.name) / "data"
        cls.data_dir.mkdir()
        cls.tree = Path(cls.tmp.name) / "tree"
        make_tree(cls.tree)
        cls.cfg = Config(cls.data_dir / "config.json")
        cls.cfg.update({"disks": [str(cls.tree)]})
        cls.db = cls.data_dir / "data.db"
        cls.httpd = make_server(cls.cfg, None, ScanStatus(), cls.db, 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, path, obj=None):
        if obj is None:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{path}") as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_scan_result_delete_flow(self):
        code, body = self.request("/api/dup/scan", {"root": str(self.tree)})
        self.assertEqual(code, 202)
        deadline = time.time() + 15
        while time.time() < deadline:
            _, st = self.request("/api/dup/status")
            if not st["running"] and st["has_result"]:
                break
            if st["error"]:
                self.fail(st["error"])
            time.sleep(0.2)
        _, result = self.request("/api/dup/result")
        self.assertTrue(result["duplicate_groups"])

        # 删除一个重复文件：应进回收站且从缓存结果中消失
        victim = result["duplicate_groups"][0]["files"][1]["path"]
        code, del_res = self.request("/api/dup/delete", {"paths": [victim]})
        self.assertEqual(code, 200)
        self.assertEqual(del_res["deleted"], [victim])
        self.assertFalse(Path(victim).exists())
        _, result2 = self.request("/api/dup/result")
        all_paths = {f["path"] for g in result2["duplicate_groups"] for f in g["files"]}
        self.assertNotIn(victim, all_paths)

        # 删除记录：可查询、带大小
        _, hist = self.request("/api/deletions")
        self.assertGreaterEqual(hist["total"], 1)
        recorded = {d["path"] for d in hist["deletions"]}
        self.assertIn(victim, recorded)
        row = next(d for d in hist["deletions"] if d["path"] == victim)
        self.assertIsNotNone(row["size"])
        self.assertGreater(hist["total_size"], 0)

    def test_scan_bad_root(self):
        try:
            self.request("/api/dup/scan", {"root": "Z:\\no-such-dir"})
            self.fail("should raise")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_delete_bad_payload(self):
        try:
            self.request("/api/dup/delete", {"paths": ["relative/path.txt"]})
            self.fail("should raise")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_delete_accepts_large_body(self):
        """批量删除路径列表超过 64KB（历史 bug 曾按 413 拒绝）。"""
        paths = [f"C:\\nonexistent\\{'很长的目录名' * 6}\\file_{i}.dat"
                 for i in range(600)]
        code, res = self.request("/api/dup/delete", {"paths": paths})
        self.assertEqual(code, 200)
        self.assertEqual(len(res["failed"]), 600)  # 路径不存在 → 全部失败但请求被接受


if __name__ == "__main__":
    unittest.main()
