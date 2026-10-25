import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from disksentry.paths import default_data_dir


class PathsTest(unittest.TestCase):
    def test_frozen_defaults_to_exe_dir(self):
        """打包后的 exe：数据目录默认在 exe 同级 data\\ 下。"""
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "DiskSentry.exe"
            exe.write_bytes(b"stub")
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(exe), create=True):
                data = default_data_dir()
            self.assertEqual(data, Path(tmp) / "data")
            self.assertTrue(data.is_dir())

    def test_frozen_unwritable_falls_back_to_localappdata(self):
        """exe 所在目录不可写时（此处用"父级是文件"构造），降级到 LOCALAPPDATA。"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "occupied.txt"
            blocker.write_text("i am a file")
            exe = blocker / "DiskSentry.exe"   # 父级是文件 -> data 目录必然建不起来
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(exe), create=True):
                data = default_data_dir()
            self.assertEqual(data, Path(os.environ["LOCALAPPDATA"]) / "DiskSentry")

    def test_source_defaults_to_localappdata(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            data = default_data_dir()
        self.assertEqual(data, Path(os.environ["LOCALAPPDATA"]) / "DiskSentry")

    def test_source_portable_uses_project_data(self):
        proj_data = Path(__file__).resolve().parents[1] / "data"
        with mock.patch.object(sys, "frozen", False, create=True):
            data = default_data_dir(portable=True)
        self.assertEqual(data, proj_data)
        self.assertTrue(data.is_dir())


if __name__ == "__main__":
    unittest.main()
