import os
import shutil
import time
import unittest

from app.api import Api
from app.models import Course, Person
from app.parser import PARSER_VERSION, parse_pdf
from app.storage import save_people


_TMP_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp", "api_test")
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PDF = os.path.join(FIXTURE_DIR, "sample_grid.pdf")


class ApiTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        os.makedirs(_TMP_DIR, exist_ok=True)
        self.data_path = os.path.join(_TMP_DIR, "people.json")
        self.groups_path = os.path.join(_TMP_DIR, "groups.json")

    def tearDown(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)

    def _api(self):
        return Api(self.data_path, self.groups_path)

    def test_get_app_info(self):
        info = self._api().get_app_info()
        self.assertEqual(info["name"], "多人空课表")
        self.assertEqual(info["version"], "2.1")
        self.assertEqual(info["developer"], "阿山")
        self.assertIn("copyright", info)
        self.assertEqual(info["parser_version"], PARSER_VERSION)
        self.assertFalse(info["needs_refresh"])

    def test_needs_refresh_when_data_parsed_with_old_algorithm(self):
        save_people(
            [
                Person(
                    name="旧数据",
                    student_id="1",
                    source=SAMPLE_PDF,
                    parsed_with=PARSER_VERSION - 1,
                )
            ],
            self.data_path,
        )
        info = self._api().get_app_info()
        self.assertTrue(info["needs_refresh"])

    def test_refresh_schedules_reparses_and_keeps_group(self):
        api = self._api()
        api.people = [
            Person(
                name="伍雅芳",
                student_id="25183032",
                semester="2026-2027学年第1学期",
                group="播音部",
                source=SAMPLE_PDF,
                parsed_with=0,
                courses=[Course(name="旧课程", day=1, start=1, end=2, weeks=[1])],
            )
        ]
        result = api.refresh_schedules()
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(result["missing"], [])
        person = api.people[0]
        self.assertEqual(person.group, "播音部")
        self.assertEqual(person.parsed_with, PARSER_VERSION)
        self.assertGreater(len(person.courses), 1)
        self.assertNotIn("旧课程", [course.name for course in person.courses])
        # 重新识别后再次读取不应再提示刷新
        self.assertFalse(self._api().get_app_info()["needs_refresh"])

    def test_refresh_schedules_reports_missing_source(self):
        api = self._api()
        api.people = [
            Person(name="找不到文件", student_id="9", source="", parsed_with=0)
        ]
        result = api.refresh_schedules()
        self.assertEqual(result["refreshed"], 0)
        self.assertEqual(result["missing"], ["找不到文件"])
        self.assertEqual(len(api.people), 1)

    def test_refresh_finds_pdf_in_common_folder(self):
        """老数据没有来源路径时，按姓名在「课表」等目录里自动找到 PDF 重新识别。"""
        folder = os.path.join(_TMP_DIR, "课表")
        os.makedirs(folder, exist_ok=True)
        shutil.copy(SAMPLE_PDF, os.path.join(folder, "伍雅芳(2026-2027-1)课表.pdf"))

        api = self._api()
        api._search_dirs = lambda: [folder]
        api.people = [
            Person(
                name="伍雅芳",
                student_id="25183032",
                semester="2026-2027学年第1学期",
                group="播音部",
                parsed_with=0,
                courses=[Course(name="旧课程", day=1, start=1, end=2, weeks=[1])],
            )
        ]
        result = api.refresh_schedules()
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(result["missing"], [])
        person = api.people[0]
        self.assertEqual(person.group, "播音部")
        self.assertEqual(person.parsed_with, PARSER_VERSION)
        self.assertTrue(person.source.endswith("伍雅芳(2026-2027-1)课表.pdf"))
        self.assertNotIn("旧课程", [course.name for course in person.courses])

    def test_locate_source_skips_other_students_file(self):
        folder = os.path.join(_TMP_DIR, "课表")
        os.makedirs(folder, exist_ok=True)
        shutil.copy(SAMPLE_PDF, os.path.join(folder, "伍雅芳(2026-2027-1)课表.pdf"))
        api = self._api()
        api._search_dirs = lambda: [folder]

        self.assertIsNone(api._locate_source(Person(name="李四", student_id="1")))
        # 姓名对上但学号不同，也不能拿来当同一个人的课表
        self.assertIsNone(api._locate_source(Person(name="伍雅芳", student_id="999")))
        self.assertIsNotNone(
            api._locate_source(Person(name="伍雅芳", student_id="25183032"))
        )

    def test_import_remembers_source_and_parser_version(self):
        api = self._api()
        api._ask_pdf_files = staticmethod(lambda: [SAMPLE_PDF])
        result = api.import_schedules()
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["people"]), 1)
        self.assertEqual(result["people"][0]["source"], SAMPLE_PDF)
        self.assertEqual(result["people"][0]["parsed_with"], PARSER_VERSION)


class FolderImportTest(unittest.TestCase):
    """文件夹导入 + 进度提示。"""

    def setUp(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        self.folder = os.path.join(_TMP_DIR, "课表")
        os.makedirs(self.folder, exist_ok=True)
        self.data_path = os.path.join(_TMP_DIR, "people.json")
        self.groups_path = os.path.join(_TMP_DIR, "groups.json")

    def tearDown(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)

    def _api(self):
        return Api(self.data_path, self.groups_path)

    def _copy(self, target_name, source=SAMPLE_PDF, subdir=""):
        directory = os.path.join(self.folder, subdir) if subdir else self.folder
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, target_name)
        shutil.copy(source, path)
        return path

    def _wait(self, api, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = api.get_import_status()
            if status["finished"] and not status["running"]:
                return status
            time.sleep(0.05)
        self.fail("导入超时")

    def test_list_folder_pdfs_uses_subfolders_and_skips_other_files(self):
        first = self._copy("伍雅芳(2026-2027-1)课表.pdf")
        second = self._copy("闫爱琳(2026-2027-1)课表.pdf", os.path.join(FIXTURE_DIR, "sample_list.pdf"), "播音部")
        with open(os.path.join(self.folder, "说明.txt"), "w", encoding="utf-8") as stream:
            stream.write("不是课表")
        found = Api._list_folder_pdfs(self.folder)
        self.assertEqual(sorted(found), sorted([first, second]))

    def test_start_import_folder_reports_progress_and_result(self):
        self._copy("伍雅芳(2026-2027-1)课表.pdf")
        self._copy("伍雅芳副本(2026-2027-1)课表.pdf")
        api = self._api()
        api._ask_pdf_folder = staticmethod(lambda: self.folder)
        started = api.start_import("folder", "播音部")
        self.assertTrue(started["started"])
        self.assertEqual(started["total"], 2)
        self.assertEqual(started["folder"], self.folder)

        status = self._wait(api)
        self.assertEqual(status["done"], status["total"])
        self.assertEqual(status["added"], 1)
        self.assertEqual(status["updated"], 1)
        self.assertEqual(status["errors"], [])
        self.assertEqual(len(api.people), 1)
        self.assertEqual(api.people[0].group, "播音部")

        # 结果要落盘，重启后仍在
        again = self._api()
        self.assertEqual(len(again.people), 1)
        self.assertEqual(again.people[0].group, "播音部")
        self.assertEqual(again.people[0].parsed_with, PARSER_VERSION)

    def test_start_import_reports_failed_files_but_keeps_going(self):
        self._copy("伍雅芳(2026-2027-1)课表.pdf")
        bad = os.path.join(self.folder, "坏文件.pdf")
        with open(bad, "w", encoding="utf-8") as stream:
            stream.write("这不是 PDF")
        api = self._api()
        api._ask_pdf_folder = staticmethod(lambda: self.folder)
        api.start_import("folder", "")
        status = self._wait(api)
        self.assertEqual(status["added"], 1)
        self.assertEqual(len(status["errors"]), 1)
        self.assertEqual(status["errors"][0]["path"], bad)

    def test_start_import_cancelled_without_folder(self):
        api = self._api()
        api._ask_pdf_folder = staticmethod(lambda: "")
        result = api.start_import("folder", "")
        self.assertFalse(result["started"])
        self.assertTrue(result["cancelled"])
        self.assertFalse(api.get_import_status()["running"])

    def test_start_import_returns_busy_when_already_running(self):
        api = self._api()
        api._import["running"] = True
        result = api.start_import("files", "")
        self.assertFalse(result["started"])
        self.assertTrue(result["busy"])

    def test_merge_replaces_same_student_id(self):
        api = self._api()
        api.people = []
        api._merge(
            Person(
                name="测试人",
                student_id="1",
                semester="2026-2027学年第1学期",
                courses=[Course(name="高等数学", day=1, start=1, end=2, weeks=[1, 2])],
            )
        )
        self.assertEqual(len(api.people), 1)
        api._merge(
            Person(
                name="测试人",
                student_id="1",
                semester="2026-2027学年第1学期",
                courses=[Course(name="大学英语", day=2, start=1, end=2, weeks=[1])],
            )
        )
        self.assertEqual(len(api.people), 1)
        self.assertEqual(api.people[0].courses[0].name, "大学英语")

    def test_merge_keeps_people_with_same_name_but_different_ids(self):
        api = self._api()
        api.people = []
        api._merge(Person(name="张伟", student_id="1001", semester="2026-2027学年第1学期"))
        api._merge(Person(name="张伟", student_id="1002", semester="2026-2027学年第1学期"))
        self.assertEqual(len(api.people), 2)
        self.assertEqual([p.student_id for p in api.people], ["1001", "1002"])

    def test_merge_falls_back_to_name_when_ids_missing(self):
        api = self._api()
        api.people = []
        api._merge(Person(name="无学号同学", semester="2026-2027学年第1学期"))
        api._merge(Person(name="无学号同学", semester="2026-2027学年第1学期"))
        self.assertEqual(len(api.people), 1)

    def test_merge_does_not_match_across_semesters(self):
        api = self._api()
        api.people = []
        api._merge(Person(name="王五", student_id="3001", semester="2026-2027学年第1学期"))
        api._merge(Person(name="王五", student_id="3001", semester="2026-2027学年第2学期"))
        self.assertEqual(len(api.people), 2)

    def test_merge_keeps_group_when_reimport_has_none(self):
        api = self._api()
        api.people = []
        api._merge(Person(name="李四", student_id="2001", semester="2026-2027学年第1学期", group="部长"))
        api._merge(Person(name="李四", student_id="2001", semester="2026-2027学年第1学期"))
        self.assertEqual(len(api.people), 1)
        self.assertEqual(api.people[0].group, "部长")

    def test_merge_uses_new_group_when_provided(self):
        api = self._api()
        api.people = []
        api._merge(Person(name="李四", student_id="2001", semester="2026-2027学年第1学期", group="部长"))
        api._merge(Person(name="李四", student_id="2001", semester="2026-2027学年第1学期", group="部员"))
        self.assertEqual(api.people[0].group, "部员")

    def test_add_and_remove_group(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        api.add_group("新闻中心")
        api.add_group("记者部", "新闻中心")
        self.assertEqual(
            api.get_groups(),
            [
                {"name": "新闻中心", "parent": ""},
                {"name": "记者部", "parent": "新闻中心"},
            ],
        )

        api.set_groups([{"index": 0, "group": "记者部"}])
        self.assertEqual(api.people[0].group, "记者部")

        api.remove_group("新闻中心")
        self.assertEqual(api.get_groups(), [])
        self.assertEqual(api.people[0].group, "")

    def test_remove_subgroup_keeps_parent(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1"), Person(name="乙", student_id="2")]
        api.add_group("新闻中心")
        api.add_group("记者部", "新闻中心")
        api.set_groups([{"index": 0, "group": "记者部"}, {"index": 1, "group": "新闻中心"}])

        api.remove_group("记者部")
        self.assertEqual(api.get_groups(), [{"name": "新闻中心", "parent": ""}])
        self.assertEqual(api.people[0].group, "")
        self.assertEqual(api.people[1].group, "新闻中心")

    def test_update_group_renames_and_keeps_members(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        api.add_group("新闻中心")
        api.add_group("记者部", "新闻中心")
        api.set_groups([{"index": 0, "group": "记者部"}])

        api.update_group("记者部", "采编部", "新闻中心")
        names = [g["name"] for g in api.get_groups()]
        self.assertIn("采编部", names)
        self.assertNotIn("记者部", names)
        self.assertEqual(api.people[0].group, "采编部")

        reloaded = self._api()
        self.assertEqual(reloaded.people[0].group, "采编部")

    def test_update_group_moves_to_another_parent(self):
        api = self._api()
        api.add_group("新闻中心")
        api.add_group("技术中心")
        api.add_group("记者部", "新闻中心")

        api.update_group("记者部", "记者部", "技术中心")
        parents = {g["name"]: g["parent"] for g in api.get_groups()}
        self.assertEqual(parents["记者部"], "技术中心")

    def test_update_group_rejects_moving_into_own_child(self):
        api = self._api()
        api.add_group("新闻中心")
        api.add_group("记者部", "新闻中心")

        api.update_group("新闻中心", "新闻中心", "记者部")
        parents = {g["name"]: g["parent"] for g in api.get_groups()}
        self.assertEqual(parents["新闻中心"], "")

    def test_update_group_rejects_duplicate_name(self):
        api = self._api()
        api.add_group("甲组")
        api.add_group("乙组")
        api.update_group("甲组", "乙组", "")
        self.assertEqual(sorted(g["name"] for g in api.get_groups()), ["乙组", "甲组"])

    def test_set_groups_registers_new_group(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        result = api.set_groups([{"index": 0, "group": "技术部"}])
        self.assertEqual(result["groups"], [{"name": "技术部", "parent": ""}])
        self.assertEqual(result["people"][0]["group"], "技术部")

    def test_reorder_groups(self):
        api = self._api()
        api.add_group("甲组")
        api.add_group("乙组")
        api.add_group("丙组")

        api.reorder_groups(["丙组", "甲组", "乙组"])
        self.assertEqual([g["name"] for g in api.get_groups()], ["丙组", "甲组", "乙组"])

        reloaded = self._api()
        self.assertEqual([g["name"] for g in reloaded.get_groups()], ["丙组", "甲组", "乙组"])

    def test_reorder_groups_ignores_unknown_names(self):
        api = self._api()
        api.add_group("甲组")
        api.add_group("乙组")
        api.reorder_groups(["乙组", "不存在"])
        self.assertEqual([g["name"] for g in api.get_groups()], ["乙组", "甲组"])

    def test_set_groups_ignores_invalid_index(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        api.set_groups([{"index": 99, "group": "记者部"}, {"index": "x", "group": "技术部"}])
        self.assertEqual(api.people[0].group, "")

    def test_remove_people_by_index(self):
        api = self._api()
        api.people = [
            Person(name="甲", student_id="1"),
            Person(name="乙", student_id="2"),
            Person(name="丙", student_id="3"),
        ]
        result = api.remove_people([0, 2])
        self.assertEqual([p.name for p in api.people], ["乙"])
        self.assertEqual([p["name"] for p in result["people"]], ["乙"])

        reloaded = self._api()
        self.assertEqual([p.name for p in reloaded.people], ["乙"])

    def test_remove_people_ignores_invalid_indices(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        api.remove_people([99, "x"])
        self.assertEqual([p.name for p in api.people], ["甲"])

    def test_remove_people_with_empty_list_keeps_everyone(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        api.remove_people([])
        self.assertEqual(len(api.people), 1)

    def test_groups_persist_to_given_paths(self):
        api = self._api()
        api.people = [Person(name="甲", student_id="1")]
        api.add_group("记者部")
        api.set_groups([{"index": 0, "group": "记者部"}])

        reloaded = self._api()
        self.assertEqual(reloaded.get_groups(), [{"name": "记者部", "parent": ""}])
        self.assertEqual(reloaded.people[0].group, "记者部")


if __name__ == "__main__":
    unittest.main()




