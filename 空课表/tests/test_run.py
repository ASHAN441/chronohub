import os
import shutil
import unittest

from run import _log_path, _run_log_path, show_error_box, write_error, write_log


_TMP_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp", "run_test")


class RunLogTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        os.makedirs(_TMP_DIR, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)

    def test_write_log_appends_lines(self):
        path = os.path.join(_TMP_DIR, "run.log")
        write_log("开始启动", path)
        write_log("正常关闭", path)
        with open(path, "r", encoding="utf-8") as stream:
            content = stream.read()
        self.assertIn("开始启动", content)
        self.assertIn("正常关闭", content)
        self.assertEqual(len(content.strip().splitlines()), 2)

    def test_write_log_ignores_broken_path(self):
        write_log("测试", os.path.join(_TMP_DIR, "不存在", "x.log"))

    def test_write_error_saves_traceback(self):
        path = os.path.join(_TMP_DIR, "error.log")
        write_error("Traceback:\nValueError: 测试错误", path)
        with open(path, "r", encoding="utf-8") as stream:
            content = stream.read()
        self.assertIn("启动失败", content)
        self.assertIn("ValueError: 测试错误", content)

    def test_default_log_paths_are_next_to_program(self):
        self.assertTrue(_log_path().endswith("启动错误.log"))
        self.assertTrue(_run_log_path().endswith("运行日志.log"))
        self.assertEqual(os.path.dirname(_log_path()), os.path.dirname(_run_log_path()))

    def test_show_error_box_passes_readable_text(self):
        calls = []
        show_error_box(ValueError("启动出错"), dialog=lambda *args: calls.append(args))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "启动出错")
        self.assertIn("多人空课表", calls[0][2])

    def test_show_error_box_never_raises(self):
        def broken_dialog(*args):
            raise OSError("没有图形界面")

        show_error_box("测试", dialog=broken_dialog)


if __name__ == "__main__":
    unittest.main()
