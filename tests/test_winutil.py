import os
import unittest
from unittest import mock

from disksentry import winutil


class DisplayLabelTest(unittest.TestCase):
    def test_prefers_volume_label(self):
        with mock.patch.object(winutil, "drive_label", return_value="Software"):
            self.assertEqual(winutil.display_label("D:\\"), "Software")

    def test_system_drive_fallback(self):
        """系统盘无卷标时兜底显示"系统盘"。"""
        with mock.patch.object(winutil, "drive_label", return_value=""), \
             mock.patch.dict(os.environ, {"SystemDrive": "C:"}):
            self.assertEqual(winutil.display_label("C:\\"), "系统盘")

    def test_non_system_drive_no_label_stays_empty(self):
        with mock.patch.object(winutil, "drive_label", return_value=""), \
             mock.patch.dict(os.environ, {"SystemDrive": "C:"}):
            self.assertEqual(winutil.display_label("E:\\"), "")


if __name__ == "__main__":
    unittest.main()
