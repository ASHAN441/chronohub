"""数据包（导出 / 导入）的单元测试。

重点验证三件事：
1. 导出的文件里**没有账号密码**
2. 一台设备导出 → 另一台设备导入，数据完全一致
3. 遇到坏文件 / 空数据包时**不破坏现有数据**
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import share_pack  # noqa: E402
from app.api import Api  # noqa: E402
from app.models import Course, Person  # noqa: E402


GROUPS = {"member": "部员", "minister": "部长"}


def make_api(root: str) -> Api:
    return Api(
        data_path=os.path.join(root, "people.json"),
        groups_path=os.path.join(root, "groups.json"),
        accounts_db_path=os.path.join(root, "accounts.db"),
    )


def seed(api: Api) -> None:
    """造两份有课的课表 + 一张值班表。"""
    api.people = []
    for index, name in enumerate(("张三", "李四"), start=1):
        person = Person(name=name, student_id=f"M{index}", group="部员")
        person.courses = [
            Course(name="高等数学", day=1, start=1, end=2, weeks=list(range(1, 17)))
        ]
        api.people.append(person)
    api.groups = [{"name": "部员", "parent": ""}, {"name": "部长", "parent": ""}]
    api.specials = [{"who": ["M1"], "days": [3], "periods": [1, 2], "weeks": [1]}]
    api.generate_duty(1, GROUPS)


class SharePackPureTest(unittest.TestCase):
    def test_build_and_load_roundtrip(self):
        package = share_pack.build(
            [{"name": "张三", "courses": [{"name": "高数"}]}],
            [{"name": "部员", "parent": ""}],
            [],
            [{"week": 3, "assignments": []}],
            [],
            app_version="3.5.1",
        )
        self.assertEqual(package["format"], share_pack.FORMAT)
        data, error = share_pack.loads(share_pack.dumps(package))
        self.assertEqual(error, "")
        self.assertEqual(data["summary"]["people"], 1)
        self.assertEqual(data["summary"]["roster_weeks"], [3])

    def test_rejects_non_package(self):
        cases = {
            "": "空",
            "hello": "JSON",
            "{}": "format",
            '{"format":"other"}': "format",
            '{"format":"chronohub-share","format_version":99,"data":{}}': "更新的版本",
            '{"format":"chronohub-share","format_version":1}': "data",
        }
        for text, keyword in cases.items():
            data, error = share_pack.loads(text)
            self.assertIsNone(data, f"{text!r} 不该被接受")
            self.assertIn(keyword, error, f"{text!r} 的报错应该提到 {keyword}")

    def test_missing_blocks_become_empty_lists(self):
        data, error = share_pack.loads(
            '{"format":"chronohub-share","format_version":1,"data":{"people":[]}}'
        )
        self.assertEqual(error, "")
        for key in share_pack.DATA_KEYS:
            self.assertEqual(data["data"][key], [])

    def test_describe(self):
        summary = share_pack.summarize_data({
            "people": [{"courses": [1, 2]}, {"courses": []}],
            "groups": [{}],
            "specials": [],
            "rosters": [{"week": 3}, {"week": 7}],
            "adjustments": [],
        })
        text = share_pack.describe(summary)
        self.assertIn("2 人", text)
        self.assertIn("3、7 周", text)

    def test_default_filename(self):
        from datetime import datetime

        name = share_pack.default_filename(datetime(2026, 11, 5, 20, 30))
        self.assertEqual(name, "ChronoHub数据-20261105-2030.json")


class SharePackApiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.pack_path = os.path.join(self.root, "pack.json")
        self.api = make_api(os.path.join(self.root, "devA"))
        seed(self.api)

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def test_export_then_import_on_another_device(self):
        # --- A 设备导出 ---
        self.api._ask_save_path = lambda *a, **k: self.pack_path
        result = self.api.export_share_pack()
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.exists(self.pack_path))
        self.assertEqual(result["summary"]["people"], 2)

        # --- B 设备导入 ---
        other = make_api(os.path.join(self.root, "devB"))
        try:
            other._ask_open_path = lambda *a, **k: self.pack_path

            preview = other.import_share_pack()
            self.assertTrue(preview["ok"], preview)
            self.assertEqual(preview["summary"]["people"], 2)

            applied = other.confirm_share_pack()
            self.assertTrue(applied["ok"], applied)
            self.assertEqual(len(other.people), 2)
            self.assertEqual([p.name for p in other.people], ["张三", "李四"])
            self.assertEqual(other.people[0].courses[0].name, "高等数学")
            self.assertEqual([r["week"] for r in other.rosters], [1])
            self.assertEqual(len(other.specials), 1)
            self.assertTrue(os.path.isdir(applied["backup"]), "导入前应该留一份备份")
        finally:
            other.accounts.close()

    def test_pack_contains_no_credentials(self):
        self.api._ask_save_path = lambda *a, **k: self.pack_path
        self.assertTrue(self.api.export_share_pack()["ok"])

        with open(self.pack_path, encoding="utf-8") as handle:
            text = handle.read()
        for word in ("password", "accounts", "dpapi", "密码"):
            self.assertNotIn(word, text.lower(), f"数据包不该包含 {word}")

    def test_import_rejects_broken_file_and_keeps_data(self):
        with open(self.pack_path, "w", encoding="utf-8") as handle:
            handle.write("这不是数据包")
        self.api._ask_open_path = lambda *a, **k: self.pack_path

        result = self.api.import_share_pack()
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])
        self.assertEqual(len(self.api.people), 2, "失败时不应该动现有数据")

    def test_import_refuses_pack_without_people(self):
        package = share_pack.build([], [], [], [], [], app_version="3.5.1")
        with open(self.pack_path, "w", encoding="utf-8") as handle:
            handle.write(share_pack.dumps(package))
        self.api._ask_open_path = lambda *a, **k: self.pack_path

        result = self.api.import_share_pack()
        self.assertFalse(result["ok"])
        self.assertIn("没有人员课表", result["error"])

    def test_confirm_without_pending_reports_error(self):
        self.api.cancel_share_pack()
        result = self.api.confirm_share_pack()
        self.assertFalse(result["ok"])
        self.assertIn("重新选择", result["error"])

    def test_import_overwrites_previous_data(self):
        self.api._ask_save_path = lambda *a, **k: self.pack_path
        self.assertTrue(self.api.export_share_pack()["ok"])

        # 当前设备只有 1 个人，导入后应该变成 2 个
        self.api.people = [Person(name="临时", student_id="X")]
        self.api.rosters = []
        self.api._ask_open_path = lambda *a, **k: self.pack_path
        self.assertTrue(self.api.import_share_pack()["ok"])
        self.assertTrue(self.api.confirm_share_pack()["ok"])
        self.assertEqual(len(self.api.people), 2)


if __name__ == "__main__":
    unittest.main()
