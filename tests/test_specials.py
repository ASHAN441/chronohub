"""手动添加的「特殊安排」的测试。

背景：课表 PDF 体现不了的占用（例会、值班、调课停课）需要用户手动补。
这些条目参与空课表统计，算错了用户会约错时间，所以存取和校验都要钉住。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import Api  # noqa: E402
from app.specials_store import (  # noqa: E402
    load_specials,
    normalize,
    person_key,
    save_specials,
)


class NormalizeTest(unittest.TestCase):
    def test_valid_entry_kept(self):
        entry = normalize({"who": ["24999186"], "days": [3], "periods": [9, 10], "weeks": [1, 2]})
        self.assertIsNotNone(entry)
        self.assertEqual(entry["days"], [3])
        self.assertEqual(entry["periods"], [9, 10])
        self.assertTrue(entry["id"])

    def test_missing_field_rejected(self):
        for broken in (
            {"who": [], "days": [3], "periods": [1], "weeks": [1]},
            {"who": ["a"], "days": [], "periods": [1], "weeks": [1]},
            {"who": ["a"], "days": [3], "periods": [], "weeks": [1]},
            {"who": ["a"], "days": [3], "periods": [1], "weeks": []},
            {},
        ):
            self.assertIsNone(normalize(broken), broken)

    def test_out_of_range_values_dropped(self):
        entry = normalize({"who": ["a"], "days": [0, 8, 3], "periods": [0, 12, 5], "weeks": [0, 19, 7]})
        self.assertEqual(entry["days"], [3])
        self.assertEqual(entry["periods"], [5])
        self.assertEqual(entry["weeks"], [7])

    def test_all_invalid_values_rejects_entry(self):
        self.assertIsNone(normalize({"who": ["a"], "days": [0], "periods": [1], "weeks": [1]}))

    def test_duplicates_removed_and_sorted(self):
        entry = normalize({"who": ["a", "a"], "days": [5, 3, 5], "periods": [2, 1, 2], "weeks": [3, 1, 3]})
        self.assertEqual(entry["who"], ["a"])
        self.assertEqual(entry["days"], [3, 5])
        self.assertEqual(entry["periods"], [1, 2])
        self.assertEqual(entry["weeks"], [1, 3])

    def test_person_key_prefers_student_id(self):
        self.assertEqual(person_key({"student_id": "24999186", "name": "张传山"}), "24999186")
        self.assertEqual(person_key({"student_id": "", "name": "张传山"}), "张传山")
        self.assertEqual(person_key({"name": "  李明  "}), "李明")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "specials.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        entries = [
            {"who": ["24999186"], "days": [3], "periods": [9, 10], "weeks": [1, 2, 3]},
            {"who": ["李明"], "days": [1, 2], "periods": [1], "weeks": list(range(1, 19))},
        ]
        save_specials(entries, self.path)
        loaded = load_specials(self.path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["days"], [3])
        self.assertEqual(loaded[1]["who"], ["李明"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_specials(os.path.join(self._tmp.name, "nope.json")), [])

    def test_corrupt_file_returns_empty(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(load_specials(self.path), [], "文件损坏不能让应用起不来")

    def test_file_written_as_valid_json(self):
        save_specials([{"who": ["a"], "days": [1], "periods": [1], "weeks": [1]}], self.path)
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertIsInstance(data.get("specials"), list)


class ApiSpecialsTest(unittest.TestCase):
    """接进 Api 之后的行为，以及和 people 数据一起落盘的隔离性。"""

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

    def test_specials_path_follows_data_path(self):
        expected = os.path.join(self._tmp.name, "specials.json")
        self.assertEqual(self.api.specials_path, expected,
                         "specials 必须跟 people 放同一目录，否则测试会污染真实数据")

    def test_starts_empty(self):
        self.assertEqual(self.api.get_specials()["specials"], [])

    def test_add_and_list(self):
        result = self.api.add_special(["24999186"], [3], [9, 10], [1, 2])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["specials"]), 1)

        listed = self.api.get_specials()["specials"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["who"], ["24999186"])
        self.assertEqual(listed[0]["periods"], [9, 10])

    def test_add_rejects_incomplete(self):
        result = self.api.add_special(["24999186"], [], [9], [1])
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])
        self.assertEqual(len(self.api.get_specials()["specials"]), 0)

    def test_persists_across_instances(self):
        self.api.add_special(["24999186"], [3], [9], [1])

        fresh = Api(
            data_path=self.api.data_path,
            groups_path=self.api.groups_path,
            accounts_db_path=os.path.join(self._tmp.name, "accounts2.db"),
        )
        try:
            loaded = fresh.get_specials()["specials"]
            self.assertEqual(len(loaded), 1, "重启后手动安排不能丢")
            self.assertEqual(loaded[0]["days"], [3])
        finally:
            fresh.accounts.close()

    def test_remove(self):
        result = self.api.add_special(["24999186"], [3], [9], [1])
        special_id = result["specials"][0]["id"]

        removed = self.api.remove_special(special_id)
        self.assertTrue(removed["ok"])
        self.assertEqual(removed["specials"], [])

        fresh = Api(
            data_path=self.api.data_path,
            groups_path=self.api.groups_path,
            accounts_db_path=os.path.join(self._tmp.name, "accounts3.db"),
        )
        try:
            self.assertEqual(fresh.get_specials()["specials"], [], "删除也要落盘")
        finally:
            fresh.accounts.close()

    def test_remove_unknown_id_is_safe(self):
        self.api.add_special(["24999186"], [3], [9], [1])
        result = self.api.remove_special("does-not-exist")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["specials"]), 1, "不该误删别的条目")

    def test_multiple_entries_kept_in_order(self):
        self.api.add_special(["a"], [1], [1], [1])
        self.api.add_special(["b"], [2], [2], [2])
        listed = self.api.get_specials()["specials"]
        self.assertEqual([e["who"] for e in listed], [["a"], ["b"]])

    def test_does_not_touch_people_file(self):
        self.api.add_special(["24999186"], [3], [9], [1])
        self.assertEqual(self.api.get_people(), [], "添加安排不应改动人员列表")


if __name__ == "__main__":
    unittest.main()
