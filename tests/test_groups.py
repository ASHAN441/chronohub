"""分组的增删改测试。

背景：分组是树形的（部员 → 技术传媒中心 …）。这里钉两件事：

1. 删分组要连子孙一起删 —— 只删父级会把下级变成"挂在空父级上的孤儿"，
   界面上既选不到也删不掉。
2. 改分组不能把父级挪进自己的下级里 —— 成环之后整棵树都乱了。

这两条一度因为方法重名（`_group_tree` 有两个同名实现）而**完全失效**：
`remove_group` 拿到的是树列表而不是名字集合，`in` 判断永远不成立，
删分组点了没反应、改上级的循环保护也从不触发。详见 tests/test_api_surface.py。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import Api  # noqa: E402
from app.models import Person  # noqa: E402


def make_api(root: str) -> Api:
    return Api(
        data_path=os.path.join(root, "people.json"),
        groups_path=os.path.join(root, "groups.json"),
        accounts_db_path=os.path.join(root, "accounts.db"),
    )


class GroupTreeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.api = make_api(self._tmp.name)
        self.api.groups = [
            {"name": "部员", "parent": ""},
            {"name": "技术传媒中心", "parent": "部员"},
            {"name": "行政事务中心", "parent": "部员"},
            {"name": "摄影组", "parent": "技术传媒中心"},
            {"name": "部长", "parent": ""},
        ]
        self.api.people = [
            Person(name="甲", student_id="1", group="摄影组"),
            Person(name="乙", student_id="2", group="技术传媒中心"),
            Person(name="丙", student_id="3", group="行政事务中心"),
            Person(name="丁", student_id="4", group="部长"),
            Person(name="戊", student_id="5", group=""),
        ]

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def names(self):
        return [g["name"] for g in self.api.groups]

    def groups_of_people(self):
        return {p.name: p.group for p in self.api.people}

    def test_scope_includes_descendants(self):
        self.assertEqual(
            self.api._group_scope("部员"),
            {"部员", "技术传媒中心", "行政事务中心", "摄影组"},
        )
        self.assertEqual(self.api._group_scope("摄影组"), {"摄影组"})
        self.assertEqual(self.api._group_scope(""), set())

    def test_remove_group_deletes_whole_subtree(self):
        self.api.remove_group("技术传媒中心")
        self.assertEqual(self.names(), ["部员", "行政事务中心", "部长"])
        people = self.groups_of_people()
        self.assertEqual(people["甲"], "", "子孙分组里的人要一起清空")
        self.assertEqual(people["乙"], "")
        self.assertEqual(people["丙"], "行政事务中心", "别的分支不该被牵连")
        self.assertEqual(people["丁"], "部长")

    def test_remove_group_persists(self):
        self.api.remove_group("摄影组")
        reloaded = make_api(self._tmp.name)
        try:
            self.assertEqual(
                [g["name"] for g in reloaded.groups], self.names()
            )
            self.assertNotIn("摄影组", [g["name"] for g in reloaded.groups])
        finally:
            reloaded.accounts.close()

    def test_remove_unknown_group_is_safe(self):
        before = self.names()
        self.api.remove_group("不存在的分组")
        self.assertEqual(self.names(), before)
        self.assertEqual(self.groups_of_people()["戊"], "")

    def test_remove_empty_name_deletes_nothing(self):
        """空名字必须什么都不删 —— 曾经会把一整棵树带走。"""
        before = self.names()
        self.api.remove_group("")
        self.assertEqual(self.names(), before)

    def test_update_group_renames_and_reparents_children(self):
        self.api.update_group("技术传媒中心", "技术中心")
        self.assertIn("技术中心", self.names())
        self.assertNotIn("技术传媒中心", self.names())

        by_name = {g["name"]: g["parent"] for g in self.api.groups}
        self.assertEqual(by_name["技术中心"], "部员", "只改名时上级应保持不变")
        self.assertEqual(by_name["摄影组"], "技术中心", "子分组要跟着改父级")
        self.assertEqual(self.groups_of_people()["乙"], "技术中心")

    def test_update_group_moves_to_top_when_parent_is_empty(self):
        self.api.update_group("摄影组", "摄影组", "")
        by_name = {g["name"]: g["parent"] for g in self.api.groups}
        self.assertEqual(by_name["摄影组"], "", "显式传空串 = 挪到顶级")

    def test_update_group_rejects_moving_under_own_child(self):
        """把「部员」挪到它的下级「摄影组」下面会让树成环。"""
        self.api.update_group("部员", "部员", "摄影组")
        by_name = {g["name"]: g["parent"] for g in self.api.groups}
        self.assertEqual(by_name["部员"], "", "成环的父级应被拒绝，退回顶级")

    def test_update_group_rejects_duplicate_name(self):
        before = self.names()
        self.api.update_group("摄影组", "部长")
        self.assertEqual(self.names(), before, "重名应整条忽略")

    def test_update_group_unknown_parent_falls_back_to_top(self):
        self.api.update_group("摄影组", "摄影组", "没有这个分组")
        by_name = {g["name"]: g["parent"] for g in self.api.groups}
        self.assertEqual(by_name["摄影组"], "")


if __name__ == "__main__":
    unittest.main()
