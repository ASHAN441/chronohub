"""课表 PDF 自动入库链路的测试。

用 空课表/tests/fixtures/ 下的真实课表样本（网格版式与列表版式各若干）验证
「PDF → 解析 → 合并进人员列表」这条合并后的核心链路。
不涉及 Qt 与网络。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import Api  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "空课表", "tests", "fixtures")

GRID = os.path.join(FIXTURES, "sample_grid.pdf")
LIST = os.path.join(FIXTURES, "sample_list.pdf")
SPAN = os.path.join(FIXTURES, "sample_span.pdf")
MULTI = os.path.join(FIXTURES, "sample_multi_page.pdf")


class ScheduleImportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.api = Api(
            data_path=os.path.join(root, "people.json"),
            groups_path=os.path.join(root, "groups.json"),
            accounts_db_path=os.path.join(root, "accounts.db"),
        )

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def test_fixtures_exist(self):
        for path in (GRID, LIST, SPAN, MULTI):
            self.assertTrue(os.path.exists(path), f"缺少测试样本：{path}")

    def test_import_grid_pdf(self):
        payload = self.api.import_schedule_pdf(GRID)
        result = payload["imported"]
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["courses"], 0, "应该解析出至少一门课")
        self.assertFalse(result["updated"], "首次导入不应算作更新")
        self.assertEqual(len(payload["people"]), 1)
        self.assertEqual(len(self.api.get_people()), 1)

    def test_import_list_pdf(self):
        payload = self.api.import_schedule_pdf(LIST)
        self.assertTrue(payload["imported"]["ok"], payload["imported"])
        self.assertGreater(payload["imported"]["courses"], 0)

    def test_reimport_same_file_updates_instead_of_duplicating(self):
        self.api.import_schedule_pdf(GRID)
        payload = self.api.import_schedule_pdf(GRID)
        self.assertTrue(payload["imported"]["ok"], payload["imported"])
        self.assertTrue(payload["imported"]["updated"], "同一人再次导入应标记为更新")
        self.assertEqual(len(payload["people"]), 1, "不应产生重复人员")

    def test_two_different_files_produce_two_people(self):
        self.api.import_schedule_pdf(GRID)
        payload = self.api.import_schedule_pdf(SPAN)
        self.assertTrue(payload["imported"]["ok"], payload["imported"])
        self.assertEqual(len(payload["people"]), 2)

    def test_missing_file_reports_error_not_crash(self):
        payload = self.api.import_schedule_pdf(
            os.path.join(self._tmp.name, "nope.pdf")
        )
        self.assertFalse(payload["imported"]["ok"])
        self.assertTrue(payload["imported"]["error"])
        self.assertEqual(payload["people"], [])

    def test_non_pdf_file_reports_error_not_crash(self):
        junk = os.path.join(self._tmp.name, "junk.pdf")
        with open(junk, "w", encoding="utf-8") as handle:
            handle.write("this is not a pdf at all")
        payload = self.api.import_schedule_pdf(junk)
        self.assertFalse(payload["imported"]["ok"])
        self.assertTrue(payload["imported"]["error"])

    def test_import_with_group_creates_group(self):
        payload = self.api.import_schedule_pdf(GRID, "记者部")
        self.assertTrue(payload["imported"]["ok"], payload["imported"])
        names = [g["name"] for g in payload["groups"]]
        self.assertIn("记者部", names)

    def test_person_gets_source_and_parser_version(self):
        self.api.import_schedule_pdf(GRID)
        person = self.api.people[0]
        self.assertEqual(person.source, GRID)
        self.assertGreater(person.parsed_with, 0)

    def test_imported_person_matches_account_by_student_id(self):
        """导入后，学号相同的账号应显示为「课表已入库」。"""
        payload = self.api.import_schedule_pdf(GRID)
        student_id = payload["imported"]["student_id"]
        if not student_id:
            self.skipTest("该样本没解析出学号，账号关联由 person.student_id 决定")

        self.api.add_account("测试", student_id, "pwd")
        rows = self.api.get_accounts()["accounts"]
        matched = [r for r in rows if r["username"] == student_id]
        self.assertEqual(len(matched), 1)
        self.assertTrue(matched[0]["imported"], "学号相同应标记课表已入库")


class BrowserStatusTest(unittest.TestCase):
    """没有 Qt 时，浏览器相关接口应安全降级而不是抛异常。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.api = Api(
            data_path=os.path.join(root, "people.json"),
            groups_path=os.path.join(root, "groups.json"),
            accounts_db_path=os.path.join(root, "accounts.db"),
        )

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def test_status_without_qt(self):
        status = self.api.get_browser_status()["browser"]
        self.assertFalse(status["open"])
        self.assertFalse(status["logged_in"])
        self.assertIn("error", status)

    def test_login_without_qt_reports_error(self):
        account_id = self.api.add_account("张三", "2023010101", "pwd")["accounts"][0]["id"]
        result = self.api.browser_login(account_id)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_export_without_qt_reports_error(self):
        self.assertFalse(self.api.browser_export()["ok"])

    def test_close_without_qt_is_safe(self):
        self.assertTrue(self.api.browser_close()["ok"])


if __name__ == "__main__":
    unittest.main()
