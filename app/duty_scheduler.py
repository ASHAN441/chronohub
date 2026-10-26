"""排值班表的算法。

规则（用户定的）
- 第 1-8 节：部员，每格 1 人（4 个两节块 × 周一到周五 = 20 个班次）
- 第 9-10 节：部长 + 台务各 1 人（5 天 = 5 个双人班次）
- 每人每周最多 1 次
- 次数按历史累计，且**只从次数最少的那批人里随机**

两个关键设计
------------
1. **不做纯随机**。纯随机会让次数严重不均（实测 5 周后每人 0~5 次）。
   先从「累计次数最少」的那批人里取候选，再在批内随机——既保留随机性
   （每次结果不同、无主观偏袒），又把次数差距压到最多 1 次。

2. **先排最难的班次**（可用人数最少的）。有些时段几乎所有人都有课，
   能选的人很少；如果先排宽松的班次，人会被提前用掉，紧的班次就空了。

可用性判断同时考虑「课表」和「手动添加的特殊安排」——用户标了没空的时段，
不该再被排上班。
"""

from __future__ import annotations

import random

from .duty_store import person_key

MEMBER_BLOCKS = [(1, 2), (3, 4), (5, 6), (7, 8)]
MINISTER_BLOCK = (9, 10)
DUTY_DAYS = range(1, 6)  # 周一到周五


def is_busy(person: dict, specials: list, key: str, day: int, start: int, end: int, week: int) -> bool:
    """该人在该时段是否没空（课表命中或手动安排命中）。"""
    for course in person.get("courses") or []:
        if course.get("day") != day:
            continue
        course_start = course.get("start") or 0
        course_end = course.get("end") or 0
        if not (course_start <= end and course_end >= start):
            continue
        if week in (course.get("weeks") or []):
            return True

    for entry in specials or []:
        if key not in (entry.get("who") or []):
            continue
        if day not in (entry.get("days") or []):
            continue
        if week not in (entry.get("weeks") or []):
            continue
        if any(start <= period <= end for period in (entry.get("periods") or [])):
            return True

    return False


def build_slots() -> list:
    """全部班次：5 天 × (4 个部员岗 + 1 个部长岗) = 25 个。

    9-10 节以前是「台务 + 部长」双岗，现在台务不值班，只剩部长一人。
    每个 slot 用 role 指明从哪个组里找人，排班逻辑因此不用再区分双岗。
    """
    slots = []
    for block_index, (start, end) in enumerate(MEMBER_BLOCKS):
        for day in DUTY_DAYS:
            slots.append({
                "kind": "member", "role": "member", "day": day, "block": block_index,
                "start": start, "end": end,
            })
    for day in DUTY_DAYS:
        slots.append({
            "kind": "minister", "role": "minister", "day": day,
            "block": len(MEMBER_BLOCKS),
            "start": MINISTER_BLOCK[0], "end": MINISTER_BLOCK[1],
        })
    return slots


def group_scope(value) -> set:
    """把「选中的分组」统一成一个名字集合。

    选父级分组时，调用方传进来的是「整棵子树」的名字集合；
    选叶子或者只有一个组时传进来的就是普通字符串。
    两种都要支持，排班逻辑才不用关心选的是哪一级。
    """
    if not value:
        return set()
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    return {str(item).strip() for item in value if str(item).strip()}


def generate_roster(person_dicts, specials, week, groups, totals, rng=None):
    """排一张值班表。

    参数
        person_dicts  人员字典列表（Person.to_dict() 的结果）
        specials      手动添加的特殊安排
        week          第几周
        groups        {"member": 部员组名, "minister": 部长组名}
        totals        历史累计次数 {人员key: 次数}（不会被修改，内部复制）
        rng           随机源，测试时可注入 random.Random(seed)

    返回
        (assignments, totals_after)
    """
    rng = rng or random.Random()
    totals = dict(totals or {})

    pools = {}
    for role in ("member", "minister"):
        scope = group_scope((groups or {}).get(role))
        pools[role] = [
            p for p in person_dicts or []
            if scope and (p.get("group") or "") in scope and person_key(p)
        ]

    used = set()

    def candidates(role, day, start, end):
        result = []
        for person in pools[role]:
            key = person_key(person)
            if key in used:
                continue
            if is_busy(person, specials, key, day, start, end, week):
                continue
            result.append(person)
        return result

    def pick(cands):
        if not cands:
            return None
        lowest = min(totals.get(person_key(p), 0) for p in cands)
        best = [p for p in cands if totals.get(person_key(p), 0) == lowest]
        return rng.choice(best)

    def difficulty(slot):
        """可用人数越少越难排（先排最紧的，避免被人抢光）。"""
        return len(candidates(slot["role"], slot["day"], slot["start"], slot["end"]))

    assignments = []
    for slot in sorted(build_slots(), key=difficulty):
        day, start, end = slot["day"], slot["start"], slot["end"]
        person = pick(candidates(slot["role"], day, start, end))
        if person:
            key = person_key(person)
            used.add(key)
            totals[key] = totals.get(key, 0) + 1
        assignments.append({
            "day": day, "block": slot["block"], "start": start, "end": end,
            "kind": slot["kind"],
            "person": person_key(person) if person else "",
        })

    return assignments, totals


def swap_candidates(person_dicts, specials, week, groups, role, day, start, end,
                    used_keys, include_used: bool = False):
    """某班次可替换的人。

    include_used=False（默认）：只要本周还没排过的人 —— 自动排班和"正常换人"用它。
    include_used=True：本周已经排过的人也算进来 —— 手动换人允许同一个人一周
    值多个班次，界面上会标出「本周已排 N 次」并二次确认。
    """
    scope = group_scope((groups or {}).get(role))
    if not scope:
        return []
    out = []
    for person in person_dicts or []:
        key = person_key(person)
        if not key:
            continue
        if key in used_keys and not include_used:
            continue
        if (person.get("group") or "") not in scope:
            continue
        if is_busy(person, specials, key, day, start, end, week):
            continue
        out.append(key)
    return sorted(out)
