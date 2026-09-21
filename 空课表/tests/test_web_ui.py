"""跑 tests/web 下的 Node 脚本，验证界面逻辑（导入进度等）。"""
import json
import os
import shutil
import subprocess
import unittest


_HERE = os.path.dirname(__file__)
_CHECK_SCRIPT = os.path.join(_HERE, "web", "ui_check.js")
_HTML_PATH = os.path.join(_HERE, "..", "web", "index.html")
_NODE = shutil.which("node")


class SidebarLayoutTest(unittest.TestCase):
    """侧栏只保留分组旁的全选，批量删除按钮放在人员列表上方。"""

    @classmethod
    def setUpClass(cls):
        with open(_HTML_PATH, "r", encoding="utf-8") as stream:
            cls.html = stream.read()

    def test_select_all_buttons_removed(self):
        self.assertNotIn("select-all-btn", self.html)
        self.assertNotIn("select-none-btn", self.html)

    def test_group_select_all_checkbox_kept(self):
        self.assertIn("全选 / 取消本组人员", self.html)

    def test_batch_remove_button_present(self):
        self.assertIn('id="people-remove-btn"', self.html)
        self.assertIn("删除勾选人员", self.html)
        self.assertIn('id="checked-count"', self.html)


@unittest.skipIf(_NODE is None, "没有安装 Node.js，跳过界面逻辑测试")
class WebUiTest(unittest.TestCase):
    def _run(self, scenario=""):
        completed = subprocess.run(
            [_NODE, _CHECK_SCRIPT] + ([scenario] if scenario else []),
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        return json.loads(completed.stdout.decode("utf-8"))

    def test_folder_import_shows_progress_and_summary(self):
        data = self._run()
        self.assertNotIn("error", data, data.get("error"))
        steps = {step["name"]: step for step in data["steps"]}

        start = steps["导入开始（1/3）"]
        self.assertTrue(start["progressVisible"])
        self.assertEqual(start["countText"], "1 / 3")
        self.assertEqual(start["fillWidth"], "33%")
        self.assertIn("a.pdf", start["noteText"])

        middle = steps["进度中（2/3）"]
        self.assertTrue(middle["progressVisible"])
        self.assertEqual(middle["countText"], "2 / 3")
        self.assertEqual(middle["fillWidth"], "67%")

        done = steps["导入完成"]
        self.assertTrue(done["progressHidden"])
        self.assertIn("新增 2 人", done["toastText"])
        self.assertIn("坏文件.pdf", done["toastText"])
        self.assertGreaterEqual(done["peopleReloaded"], 2)

    def test_folder_import_calls_backend_with_group(self):
        data = self._run()
        self.assertIn("start_import:folder:记者部", data["calls"])
        self.assertIn("get_import_status", data["calls"])

    def test_batch_remove_checked_people(self):
        data = self._run("delete")
        self.assertNotIn("error", data, data.get("error"))
        steps = {step["name"]: step for step in data["steps"]}

        self.assertEqual(steps["初始勾选提示"]["checkedCountText"], "已勾选 2 人")

        clicked = steps["点击删除"]
        self.assertTrue(clicked["confirmShown"])
        self.assertIn("确定删除勾选的 2 人", clicked["confirmText"])
        self.assertIn("伍雅芳", clicked["confirmText"])

        done = steps["确认删除"]
        self.assertTrue(done["confirmHidden"])
        self.assertEqual(done["toastText"], "已删除 2 人")
        self.assertIn("remove_people:0,1", data["calls"])


if __name__ == "__main__":
    unittest.main()
