import os
import tempfile
import unittest
from pathlib import Path

from disksentry.scanner import iter_files


def make_tree(base: Path):
    (base / "d1").mkdir(parents=True)
    (base / "d1" / "d2").mkdir()
    (base / "a.txt").write_bytes(b"0123456789")            # 10
    (base / "d1" / "b.bin").write_bytes(b"x" * 20)          # 20
    (base / "d1" / "d2" / "c.log").write_bytes(b"y" * 5)    # 5


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        make_tree(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def _norm(self, root):
        return os.path.normcase(str(root)).rstrip("\\") + "\\"

    def test_walk_all_files(self):
        got = {os.path.relpath(p, self.base): s
               for p, s, m in iter_files(str(self.base))}
        self.assertEqual(got, {"a.txt": 10, os.path.join("d1", "b.bin"): 20,
                               os.path.join("d1", "d2", "c.log"): 5})

    def test_counter(self):
        stats = {}
        list(iter_files(str(self.base), counter=stats))
        self.assertEqual(stats["entries"], 3)

    def test_exclude(self):
        excl = [str(self.base / "d1" / "d2")]
        got = {os.path.relpath(p, self.base): s
               for p, s, m in iter_files(str(self.base), excludes=excl)}
        self.assertEqual(sorted(got), ["a.txt", os.path.join("d1", "b.bin")])

    def test_detects_growth_between_scans(self):
        f = self.base / "a.txt"
        first = max(iter_files(str(self.base)), key=lambda e: e[0].endswith("a.txt"))
        f.write_bytes(b"0" * 40)
        second = max((e for e in iter_files(str(self.base)) if e[0].endswith("a.txt")),
                     key=lambda e: e[1])
        self.assertEqual(second[1] - first[1], 30)


if __name__ == "__main__":
    unittest.main()
