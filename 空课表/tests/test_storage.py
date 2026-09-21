import json
import os
import shutil
import unittest

from app.models import Course, Person
from app.storage import (
    DATA_FILE,
    load_data,
    load_groups,
    load_people,
    save_groups,
    save_people,
    storage_path,
)


_TMP_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp", "storage_test")


class StorageTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        os.makedirs(_TMP_DIR, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)

    def _path(self, name):
        return os.path.join(_TMP_DIR, name)

    def test_person_round_trip(self):
        person = Person(
            name="张三",
            student_id="123",
            semester="2026-2027学年第1学期",
            group="记者部",
            courses=[
                Course(
                    name="高等数学",
                    day=1,
                    start=1,
                    end=2,
                    weeks=[1, 2, 3],
                    location="A101",
                    teacher="王老师",
                )
            ],
        )
        self.assertEqual(Person.from_dict(person.to_dict()), person)

    def test_group_defaults_to_empty_when_missing(self):
        person = Person.from_dict({"name": "李四"})
        self.assertEqual(person.group, "")

    def test_save_and_load(self):
        person = Person(name="张三", student_id="123", semester="2026-2027学年第1学期")
        path = self._path("people.json")
        save_people([person], path)
        self.assertTrue(os.path.exists(path))

        loaded = load_people(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "张三")
        self.assertEqual(loaded[0].student_id, "123")

    def test_save_keeps_source_and_parser_version(self):
        person = Person(
            name="张三",
            student_id="123",
            source=r"C:\课表\张三.pdf",
            parsed_with=2,
        )
        path = self._path("people.json")
        save_people([person], path)
        loaded, version = load_data(path)
        self.assertEqual(loaded[0].source, r"C:\课表\张三.pdf")
        self.assertEqual(loaded[0].parsed_with, 2)
        self.assertEqual(version, 2)

    def test_load_legacy_people_list(self):
        path = self._path("legacy_people.json")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps([{"name": "李四", "student_id": "9"}]))
        loaded, version = load_data(path)
        self.assertEqual(loaded[0].name, "李四")
        self.assertEqual(loaded[0].source, "")
        self.assertEqual(version, 0)

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(load_people(self._path("missing.json")), [])

    def test_groups_round_trip(self):
        path = self._path("groups.json")
        save_groups(
            [
                {"name": "新闻中心", "parent": ""},
                {"name": "记者部", "parent": "新闻中心"},
            ],
            path,
        )
        self.assertEqual(
            load_groups(path),
            [
                {"name": "新闻中心", "parent": ""},
                {"name": "记者部", "parent": "新闻中心"},
            ],
        )

    def test_load_legacy_string_groups(self):
        path = self._path("legacy_groups.json")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(["记者部"], ensure_ascii=False))
        self.assertEqual(load_groups(path), [{"name": "记者部", "parent": ""}])

    def test_load_missing_groups_returns_empty(self):
        self.assertEqual(load_groups(self._path("missing_groups.json")), [])

    def test_data_file_location_when_not_frozen(self):
        self.assertTrue(DATA_FILE.replace("\\", "/").endswith("/data/people.json"))

    def test_storage_path_lives_under_data_dir(self):
        path = storage_path()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.path.dirname(path), os.path.dirname(DATA_FILE))


if __name__ == "__main__":
    unittest.main()
