"""CLI 参数位置回归测试。

历史 bug：argparse 子解析器解析后把自身命名空间整批拷回主命名空间，
公共参数写在子命令之前时会被子解析器的默认值覆盖（--data-dir 曾被静默丢弃，
导致写错真实数据目录）。此测试保证"子命令前 / 子命令后"两种写法等价。
"""
import logging
import tempfile
import unittest
from pathlib import Path

from disksentry.__main__ import main


class CliArgsPlacementTest(unittest.TestCase):
    @staticmethod
    def _close_log_handlers():
        """main() 会在当前进程挂文件 handler，Windows 下不关掉会锁住日志文件。"""
        root = logging.getLogger()
        for h in root.handlers[:]:
            h.close()
            root.removeHandler(h)

    def _run_main(self, argv):
        try:
            return main(argv)
        finally:
            self._close_log_handlers()

    def _assert_data_dir_used(self, data_dir: Path):
        self.assertTrue((data_dir / "data.db").exists(),
                        "数据库未建在指定的 --data-dir 下")
        self.assertTrue((data_dir / "disksentry.log").exists(),
                        "日志未建在指定的 --data-dir 下")

    def test_data_dir_before_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._run_main(["--data-dir", str(d), "scan", "--root",
                            str(d / "no-such-root")])
            self._assert_data_dir_used(d)

    def test_data_dir_after_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._run_main(["scan", "--root", str(d / "no-such-root"),
                            "--data-dir", str(d)])
            self._assert_data_dir_used(d)

    def test_bare_with_data_dir_no_clobber(self):
        """裸调用 + 公共参数 + 子命令混排：--data-dir 不被覆盖。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            rc = self._run_main(["--data-dir", str(d), "--portable", "--verbose",
                                 "scan", "--root", str(d / "no-such-root")])
            self.assertEqual(rc, 1)  # 根不存在：scan 记为失败，但数据目录必须正确
            self._assert_data_dir_used(d)


if __name__ == "__main__":
    unittest.main()
