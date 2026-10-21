"""排值班表的测试。

规则来自用户：1-8 节部员、9-10 节部长+台务、每人每周最多 1 次、次数累计历史。
排班算错会让某人连值多天或被漏掉，所以这些约束都要钉住。
"""

import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import Api  # noqa: E402
from app.duty_scheduler import generate_roster, swap_candidates  # noqa: E402
from app.duty_store import (  # noqa: E402
    count_assignments,
    load_rosters,
    normalize_roster,
    save_rosters,
    upsert_roster,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "值班表.docx")


def person(name, sid, group, courses=None):
    return {"name": name, "student_id": sid, "group": group, "courses": courses or []}


def course(day, start, end, weeks):
    return {"day": day, "start": start, "end": end, "weeks": weeks}


def make_people(n_member=25, n_minister=6):
    people = []
    for i in range(n_member):
        people.append(person(f"部员{i}", f"M{i}", "部员"))
    for i in range(n_minister):
        people.append(person(f"部长{i}", f"N{i}", "部长"))
    return people


# 台务不再参与排班（2026-10-21），9-10 节只剩部长
GROUPS = {"member": "部员", "minister": "部长"}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "duty.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        roster = {
            "week": 3,
            "groups": GROUPS,
            "assignments": [
                {"day": 1, "block": 0, "start": 1, "end": 2, "kind": "member", "person": "M0"},
                {"day": 1, "block": 4, "start": 9, "end": 10, "kind": "minister", "person": "N0"},
            ],
        }
        save_rosters([roster], self.path)
        loaded = load_rosters(self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["week"], 3)
        self.assertEqual(len(loaded[0]["assignments"]), 2)

    def test_old_dual_data_is_migrated(self):
        """旧数据里 9-10 节是 kind=dual 的「台务+部长」，读的时候要能转过来。

        台务取消后统一成单人：取当年 minister 当 person，hall 丢弃。
        不迁移的话老值班表会读不出来（或读成空白）。
        """
        old = {
            "week": 1,
            "groups": {"member": "部员", "minister": "部长", "hall": "台务"},
            "assignments": [
                {"day": 1, "block": 4, "start": 9, "end": 10, "kind": "dual",
                 "minister": "N0", "hall": "H0"},
            ],
        }
        save_rosters([old], self.path)
        loaded = load_rosters(self.path)
        item = loaded[0]["assignments"][0]
        self.assertEqual(item["kind"], "minister")
        self.assertEqual(item["person"], "N0", "应保留当年的部长")
        self.assertNotIn("hall", item, "台务字段应该被丢掉")
        self.assertNotIn("hall", loaded[0]["groups"])

    def test_corrupt_file_returns_empty(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{oops")
        self.assertEqual(load_rosters(self.path), [])

    def test_upsert_replaces_same_week(self):
        a = {"week": 3, "assignments": [
            {"day": 1, "block": 0, "start": 1, "end": 2, "kind": "member", "person": "A"}]}
        b = {"week": 3, "assignments": [
            {"day": 2, "block": 0, "start": 1, "end": 2, "kind": "member", "person": "B"}]}
        c = {"week": 4, "assignments": [
            {"day": 1, "block": 0, "start": 1, "end": 2, "kind": "member", "person": "C"}]}
        rosters = upsert_roster(upsert_roster([a], b), c)
        self.assertEqual([r["week"] for r in rosters], [3, 4])
        week3 = [r for r in rosters if r["week"] == 3][0]
        self.assertEqual(week3["assignments"][0]["person"], "B")

    def test_counts_and_exclude_week(self):
        rosters = [
            {"week": 1, "assignments": [
                {"day": 1, "block": 0, "start": 1, "end": 2, "kind": "member", "person": "A"},
                {"day": 1, "block": 4, "start": 9, "end": 10, "kind": "minister", "person": "B"}]},
            {"week": 2, "assignments": [
                {"day": 2, "block": 0, "start": 1, "end": 2, "kind": "member", "person": "A"}]},
        ]
        counts = count_assignments(rosters)
        self.assertEqual(counts["A"], 2)
        self.assertEqual(counts["B"], 1)
        self.assertNotIn("C", counts, "台务不该再被统计")

        # 重排第 2 周时，第 2 周的历史不该算进去
        counts = count_assignments(rosters, exclude_week=2)
        self.assertEqual(counts["A"], 1)

    def test_normalize_rejects_bad_week(self):
        self.assertIsNone(normalize_roster({"week": 0, "assignments": []}))
        self.assertIsNone(normalize_roster({"week": "x", "assignments": []}))

    def test_normalize_keeps_empty_slots(self):
        """没人可排的格子要保留下来（前端才能显示「缺」）。"""
        roster = normalize_roster({
            "week": 1,
            "assignments": [
                {"day": 1, "block": 0, "start": 1, "end": 2, "kind": "member", "person": ""}],
        })
        self.assertIsNotNone(roster)
        self.assertEqual(roster["assignments"][0]["person"], "")


class SchedulerTest(unittest.TestCase):
    def test_fills_everything_when_enough_people(self):
        people = make_people()
        assignments, totals = generate_roster(
            people, [], 1, GROUPS, {}, random.Random(1))
        self.assertEqual(len(assignments), 25)

        member_slots = [a for a in assignments if a["kind"] == "member"]
        minister_slots = [a for a in assignments if a["kind"] == "minister"]
        self.assertEqual(len(member_slots), 20)
        self.assertEqual(len(minister_slots), 5)
        self.assertTrue(all(a["person"] for a in member_slots), "部员岗应全部排满")
        self.assertTrue(all(a["person"] for a in minister_slots), "9-10节部长岗应全部排满")
        self.assertFalse(any(a["kind"] == "dual" for a in assignments), "不该再有双岗")

    def test_weekly_limit_one(self):
        people = make_people()
        assignments, _ = generate_roster(people, [], 1, GROUPS, {}, random.Random(2))
        seen = {}
        for item in assignments:
            key = item.get("person")
            if key:
                seen[key] = seen.get(key, 0) + 1
        self.assertEqual(max(seen.values()), 1, "一个人一周不该被排两次")

    def test_skips_busy_people(self):
        """只有一个人有空时，必须排他；其他人全有课就排不上。"""
        people = [
            person("闲人", "A", "部员", []),
            person("满课", "B", "部员", [course(d, 1, 10, list(range(1, 19))) for d in range(1, 6)]),
        ]
        assignments, _ = generate_roster(
            people, [], 1, {"member": "部员", "minister": ""}, {}, random.Random(3))
        member = [a for a in assignments if a["kind"] == "member"]
        self.assertEqual(sum(1 for a in member if a["person"] == "A"), 1)
        self.assertFalse(any(a["person"] == "B" for a in member), "满课的人不该被排")
        self.assertEqual(sum(1 for a in member if not a["person"]), 19)

    def test_skips_manually_blocked_people(self):
        """手动添加的特殊安排也要算没空。"""
        people = [person("闲人", "A", "部员", [])]
        specials = [{"who": ["A"], "days": [1], "periods": [1, 2],
                     "weeks": list(range(1, 19))}]
        assignments, _ = generate_roster(
            people, specials, 1, {"member": "部员", "minister": "", "hall": ""}, {}, random.Random(4))
        monday_first = [a for a in assignments if a["kind"] == "member" and a["day"] == 1
                        and a["start"] == 1][0]
        self.assertEqual(monday_first["person"], "", "被手动标为没空的人不该排上")

    def test_missing_group_leaves_slots_empty(self):
        people = make_people()
        assignments, _ = generate_roster(
            people, [], 1, {"member": "", "minister": "", "hall": ""}, {}, random.Random(5))
        member = [a for a in assignments if a["kind"] == "member"]
        self.assertEqual(sum(1 for a in member if a.get("person")), 0)

    def test_prefers_lowest_count(self):
        """历史值过很多次的人，本轮应该轮空。"""
        people = [person(f"P{i}", f"K{i}", "部员", []) for i in range(21)]
        totals = {f"K{i}": (9 if i == 0 else 0) for i in range(21)}
        assignments, _ = generate_roster(
            people, [], 1, {"member": "部员", "minister": "", "hall": ""}, totals, random.Random(6))
        member = [a for a in assignments if a["kind"] == "member"]
        assigned = [a["person"] for a in member if a.get("person")]
        self.assertEqual(len(assigned), 20)
        self.assertNotIn("K0", assigned, "累计 9 次的人不该再排")

    def test_swap_candidates_excludes_busy_and_used(self):
        people = [
            person("有空", "A", "部员", []),
            person("有课", "B", "部员", [course(1, 1, 2, list(range(1, 19)))]),
            person("已排", "C", "部员", []),
        ]
        result = swap_candidates(people, [], 1, {"member": "部员"},
                                 "member", 1, 1, 2, {"C"})
        self.assertEqual(result, ["A"])


class ApiDutyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.api = Api(
            data_path=os.path.join(root, "people.json"),
            groups_path=os.path.join(root, "groups.json"),
            accounts_db_path=os.path.join(root, "accounts.db"),
        )
        # 直接塞人员（不依赖 PDF）
        from app.models import Person

        self.api.people = []
        for i in range(25):
            self.api.people.append(Person(name=f"部员{i}", student_id=f"M{i}", group="部员"))
        for i in range(6):
            self.api.people.append(Person(name=f"部长{i}", student_id=f"N{i}", group="部长"))
        for i in range(5):
            self.api.people.append(Person(name=f"台务{i}", student_id=f"H{i}", group="台务"))

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def test_duty_path_follows_data_path(self):
        self.assertEqual(
            self.api.duty_path,
            os.path.join(self._tmp.name, "duty.json"),
            "duty 必须跟 people 同目录，否则测试会污染真实数据",
        )

    def test_generate_and_persist(self):
        result = self.api.generate_duty(1, GROUPS)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["rosters"]), 1)
        self.assertEqual(result["rosters"][0]["week"], 1)

        fresh = Api(
            data_path=self.api.data_path,
            groups_path=self.api.groups_path,
            accounts_db_path=os.path.join(self._tmp.name, "accounts2.db"),
        )
        try:
            self.assertEqual(len(fresh.get_duty()["rosters"]), 1, "重启后排班不能丢")
        finally:
            fresh.accounts.close()

    def test_generate_requires_people(self):
        empty = Api(
            data_path=os.path.join(self._tmp.name, "empty.json"),
            groups_path=os.path.join(self._tmp.name, "g2.json"),
            accounts_db_path=os.path.join(self._tmp.name, "a3.db"),
        )
        try:
            result = empty.generate_duty(1, GROUPS)
            self.assertFalse(result["ok"])
            self.assertIn("课表", result["error"])
        finally:
            empty.accounts.close()

    def test_counts_accumulate_across_weeks(self):
        self.api.generate_duty(1, GROUPS)
        self.api.generate_duty(2, GROUPS)
        payload = self.api.get_duty()
        total = sum(payload["counts"].values())
        # 每周 20 个部员岗 + 5 个部长岗 = 25 人次，两周共 50
        # （以前 9-10 节是台务+部长双岗，每周 30 人次，取消台务后变 25）
        self.assertEqual(total, 50)

    def test_swap_and_candidates(self):
        self.api.generate_duty(1, GROUPS)
        payload = self.api.get_duty()
        roster = payload["rosters"][0]
        slot = [a for a in roster["assignments"] if a["kind"] == "member"][0]

        cands = self.api.duty_candidates(1, slot["day"], slot["block"], "member")["candidates"]
        self.assertTrue(cands, "应该至少有一个可换的人")
        target = cands[0]["key"]

        result = self.api.swap_duty(1, slot["day"], slot["block"], "member", target)
        self.assertTrue(result["ok"])
        updated = [a for a in result["rosters"][0]["assignments"]
                   if a["kind"] == "member" and a["day"] == slot["day"]
                   and a["block"] == slot["block"]][0]
        self.assertEqual(updated["person"], target)

    def test_swap_rejects_unknown_week(self):
        result = self.api.swap_duty(9, 1, 0, "member", "M0")
        self.assertFalse(result["ok"])

    def test_swap_rejects_busy_person(self):
        """换人接口必须自己校验可用性——前端只给合法候选，但接口不能不设防。"""
        self.api.generate_duty(1, GROUPS)
        roster = self.api.get_duty()["rosters"][0]
        slot = [a for a in roster["assignments"] if a["kind"] == "member"][0]

        from app.models import Course

        used = {a.get("person") for a in roster["assignments"] if a.get("person")}
        spare = [p for p in self.api.people
                 if p.group == "部员" and p.student_id not in used][0]
        spare.courses = [Course(name="全覆盖的课", day=slot["day"], start=1, end=11,
                                weeks=list(range(1, 19)))]

        result = self.api.swap_duty(1, slot["day"], slot["block"], "member", spare.student_id)
        self.assertFalse(result["ok"])
        self.assertIn("没空", result["error"])

    def test_swap_rejects_double_booking(self):
        """每周最多 1 次的限制，换人时也必须守住。"""
        self.api.generate_duty(1, GROUPS)
        roster = self.api.get_duty()["rosters"][0]
        slots = [a for a in roster["assignments"]
                 if a["kind"] == "member" and a.get("person")]
        self.assertGreaterEqual(len(slots), 2)
        target, other = slots[0], slots[1]

        result = self.api.swap_duty(1, target["day"], target["block"], "member", other["person"])
        self.assertFalse(result["ok"])
        self.assertIn("每周最多", result["error"])

    def test_swap_rejects_wrong_role(self):
        """双岗只能换部长/台务，单岗只能换部员。"""
        self.api.generate_duty(1, GROUPS)
        roster = self.api.get_duty()["rosters"][0]
        dual = [a for a in roster["assignments"] if a["kind"] == "minister"][0]

        # 部长岗不能塞部员
        result = self.api.swap_duty(1, dual["day"], dual["block"], "member", "M0")
        self.assertFalse(result["ok"])
        self.assertIn("只能换部长", result["error"])

        # 台务这个角色已经不存在了
        result = self.api.swap_duty(1, dual["day"], dual["block"], "hall", "M0")
        self.assertFalse(result["ok"])

    def test_clear_week(self):
        self.api.generate_duty(1, GROUPS)
        result = self.api.clear_duty(1)
        self.assertTrue(result["ok"])
        self.assertEqual(self.api.get_duty()["rosters"], [])

    def test_counts_exclude_current_week_when_regenerating(self):
        """重排同一周：这一周已排的人不该被当成「值过班」而轮空。"""
        self.api.generate_duty(1, GROUPS)
        first = self.api.get_duty()["counts"]
        self.assertTrue(all(v == 1 for v in first.values()))

        self.api.generate_duty(1, GROUPS)
        second = self.api.get_duty()["counts"]
        self.assertTrue(all(v == 1 for v in second.values()),
                        "覆盖同一周后，次数仍应是每人 1 次")


class AdjustTest(unittest.TestCase):
    """手动修改值班次数：必须留记录、能撤销，且不能被排班动作冲掉。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.api = Api(
            data_path=os.path.join(self._tmp.name, "people.json"),
            groups_path=os.path.join(self._tmp.name, "groups.json"),
            accounts_db_path=os.path.join(self._tmp.name, "accounts.db"),
        )
        from app.models import Person

        self.api.people = []
        for i in range(25):
            self.api.people.append(Person(name=f"部员{i}", student_id=f"M{i}", group="部员"))
        for i in range(6):
            self.api.people.append(Person(name=f"部长{i}", student_id=f"N{i}", group="部长"))
        for i in range(5):
            self.api.people.append(Person(name=f"台务{i}", student_id=f"H{i}", group="台务"))
        self.api.generate_duty(1, GROUPS)

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def _some_member(self) -> str:
        roster = self.api.get_duty()["rosters"][0]
        return [a["person"] for a in roster["assignments"]
                if a["kind"] == "member" and a.get("person")][0]

    def test_adjust_path_isolated(self):
        self.assertEqual(
            self.api.adjust_path,
            os.path.join(self._tmp.name, "duty_adjust.json"),
            "手动调整要跟数据放一起，测试不能污染真实数据",
        )

    def test_adjust_adds_to_total(self):
        key = self._some_member()
        before = self.api.get_duty()
        base = before["counts"][key]

        result = self.api.adjust_duty(key, 2, "补记上学期")
        self.assertTrue(result["ok"])
        self.assertEqual(result["counts"][key], base + 2)
        self.assertEqual(result["roster_counts"][key], base, "排班统计不该被改")
        self.assertEqual(result["manual_counts"][key], 2)
        self.assertEqual(len(result["adjustments"]), 1)
        self.assertEqual(result["adjustments"][0]["note"], "补记上学期")

    def test_adjust_negative(self):
        key = self._some_member()
        base = self.api.get_duty()["counts"][key]
        result = self.api.adjust_duty(key, -1)
        self.assertEqual(result["counts"][key], base - 1)

    def test_adjust_persists_across_instances(self):
        key = self._some_member()
        self.api.adjust_duty(key, 3)

        fresh = Api(
            data_path=self.api.data_path,
            groups_path=self.api.groups_path,
            accounts_db_path=os.path.join(self._tmp.name, "a2.db"),
        )
        try:
            payload = fresh.get_duty()
            self.assertEqual(payload["manual_counts"].get(key), 3)
        finally:
            fresh.accounts.close()

    def test_adjust_survives_regenerate(self):
        """重新排班（甚至换人）不能把手动记录冲掉。"""
        key = self._some_member()
        self.api.adjust_duty(key, 2, "补记")

        self.api.generate_duty(1, GROUPS)
        payload = self.api.get_duty()
        self.assertEqual(payload["manual_counts"].get(key), 2, "手动记录必须保留")
        self.assertEqual(len(payload["adjustments"]), 1)

    def test_adjust_does_not_touch_rosters(self):
        key = self._some_member()
        before = json.dumps(self.api.get_duty()["rosters"], sort_keys=True)
        self.api.adjust_duty(key, 1)
        after = json.dumps(self.api.get_duty()["rosters"], sort_keys=True)
        self.assertEqual(before, after, "调次数不该改动排班本身")

    def test_undo_adjustment(self):
        key = self._some_member()
        base = self.api.get_duty()["counts"][key]
        added = self.api.adjust_duty(key, 2)
        adjust_id = added["adjustments"][0]["id"]

        result = self.api.undo_adjustment(adjust_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["counts"][key], base)
        self.assertEqual(result["adjustments"], [])

    def test_undo_unknown_id_fails(self):
        result = self.api.undo_adjustment("nope")
        self.assertFalse(result["ok"])

    def test_rejects_invalid_adjustments(self):
        key = self._some_member()
        self.assertFalse(self.api.adjust_duty(key, 0)["ok"])
        self.assertFalse(self.api.adjust_duty(key, "x")["ok"])
        self.assertFalse(self.api.adjust_duty(key, 500)["ok"])
        self.assertFalse(self.api.adjust_duty("不存在的人", 1)["ok"])
        self.assertFalse(self.api.adjust_duty("", 1)["ok"])
        self.assertEqual(self.api.get_duty()["adjustments"], [])


class ExportTest(unittest.TestCase):
    """导出要真的填进模板，而不是生成一份看起来像的新文档。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.api = Api(
            data_path=os.path.join(self._tmp.name, "people.json"),
            groups_path=os.path.join(self._tmp.name, "groups.json"),
            accounts_db_path=os.path.join(self._tmp.name, "accounts.db"),
        )
        from app.models import Person

        self.api.people = []
        for i in range(25):
            self.api.people.append(Person(name=f"部员{i}", student_id=f"M{i}", group="部员"))
        for i in range(6):
            self.api.people.append(Person(name=f"部长{i}", student_id=f"N{i}", group="部长"))
        for i in range(5):
            self.api.people.append(Person(name=f"台务{i}", student_id=f"H{i}", group="台务"))

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def test_export_fills_template(self):
        if not os.path.exists(TEMPLATE):
            self.skipTest("缺少值班表.docx 模板")

        self.api.generate_duty(3, GROUPS)
        out = os.path.join(self._tmp.name, "第3周值班表.docx")
        result = self.api.export_duty(3, out)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(os.path.exists(out))

        from docx import Document

        document = Document(out)
        # 标题应填了周次
        titles = [p.text for p in document.paragraphs if "值班记录" in p.text]
        self.assertTrue(titles, "应保留标题段落")
        self.assertIn("（3）", titles[0])

        # 表格里应出现人名
        table = document.tables[0]
        texts = []
        for row in table.rows:
            for cell in row.cells:
                texts.append(cell.text)
        joined = "\n".join(texts)
        self.assertIn("部员", joined, "表格里应填入部员姓名")
        self.assertIn("部长：", joined, "9-10 节应填成「部长：x」")
        self.assertNotIn("台务", joined, "台务不该再出现在导出的表里")
        self.assertIn("(部长)", joined, "模板标签应改成「(部长)」")

        # 注意事项要原样保留
        self.assertIn("信息楼108", titles[0])

    def test_export_font_matches_template(self):
        """空格子填入的人名必须继承模板的字体，不能掉到文档默认字体。

        实测踩过：模板表格是黑体 12pt，空格子没有 run，直接 add_run
        出来是 Arial 默认字体，打出来和整张表不协调。
        """
        if not os.path.exists(TEMPLATE):
            self.skipTest("缺少值班表.docx 模板")

        self.api.generate_duty(3, GROUPS)
        out = os.path.join(self._tmp.name, "font.docx")
        result = self.api.export_duty(3, out)
        self.assertTrue(result["ok"], result.get("error"))

        from docx import Document
        from docx.oxml.ns import qn

        document = Document(out)
        table = document.tables[0]

        # 找一个填了人名的单岗格子
        filled = None
        for row in table.rows[1:]:
            for cell in row.cells[1:]:
                text = (cell.text or "").strip()
                if text and "台务" not in text:
                    filled = cell
                    break
            if filled:
                break
        self.assertIsNotNone(filled, "应至少有一个填了人名的格子")

        run = filled.paragraphs[0].runs[0]
        self.assertIsNotNone(run.font.element.rPr, "填入的文字应有格式属性")
        east_asia = run.font.element.rPr.rFonts.get(qn("w:eastAsia"))
        self.assertEqual(east_asia, "黑体", "中文字体应继承模板的黑体")
        self.assertEqual(run.font.size.pt, 12.0, "字号应继承模板的 12pt")

    def test_export_missing_week(self):
        result = self.api.export_duty(7, os.path.join(self._tmp.name, "x.docx"))
        self.assertFalse(result["ok"])

    def test_exports_dir_is_isolated(self):
        """兜底导出目录要跟数据放一起，不能写到真实 data/ 里。"""
        self.assertEqual(self.api.exports_dir, os.path.join(self._tmp.name, "exports"))

    def test_export_falls_back_when_target_unwritable(self):
        """存到不可写的位置时，不能直接失败——退回到导出目录并说明原因。

        用户实测遇到的就是这一类：保存对话框挑了个写不进去的地方，
        结果只看到一句"导出失败"，文件没出来。
        """
        if not os.path.exists(TEMPLATE):
            self.skipTest("缺少值班表.docx 模板")

        self.api.generate_duty(3, GROUPS)

        # 用一个"同名文件"占住目录位置，让 makedirs 必然失败
        blocker = os.path.join(self._tmp.name, "blocked")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        bad_target = os.path.join(blocker, "值班表.docx")

        result = self.api.export_duty(3, bad_target)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result.get("note"), "应该告诉用户改存到哪了")
        self.assertTrue(os.path.exists(result["path"]))
        self.assertTrue(
            os.path.abspath(result["path"]).startswith(os.path.abspath(self.api.exports_dir)),
            "兜底文件必须落在导出目录里",
        )


if __name__ == "__main__":
    unittest.main()
