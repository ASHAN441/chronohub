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
DUAL_BLOCK = (9, 10)
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
    """全部班次：20 个单岗 + 5 个双岗。"""
    slots = []
    for block_index, (start, end) in enumerate(MEMBER_BLOCKS):
        for day in DUTY_DAYS:
            slots.append({
                "kind": "member", "day": day, "block": block_index,
                "start": start, "end": end,
            })
    for day in DUTY_DAYS:
        slots.append({
            "kind": "dual", "day": day, "block": len(MEMBER_BLOCKS),
            "start": DUAL_BLOCK[0], "end": DUAL_BLOCK[1],
        })
    return slots


def generate_roster(person_dicts, specials, week, groups, totals, rng=None):
    """排一张值班表。

    参数
        person_dicts  人员字典列表（Person.to_dict() 的结果）
        specials      手动添加的特殊安排
        week          第几周
        groups        {"member": 组名, "minister": 组名, "hall": 组名}
        totals        历史累计次数 {人员key: 次数}（不会被修改，内部复制）
        rng           随机源，测试时可注入 random.Random(seed)

    返回
        (assignments, totals_after)
    """
    rng = rng or random.Random()
    totals = dict(totals or {})

    pools = {}
    for role in ("member", "minister", "hall"):
        name = (groups or {}).get(role) or ""
        pools[role] = [
            p for p in person_dicts or []
            if name and (p.get("group") or "") == name and person_key(p)
        ] if name else []

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
        """可用人数越少越难排。双岗取两个角色里更紧的那个。"""
        if slot["kind"] == "dual":
            return min(
                len(candidates("minister", slot["day"], slot["start"], slot["end"])),
                len(candidates("hall", slot["day"], slot["start"], slot["end"])),
            )
        return len(candidates("member", slot["day"], slot["start"], slot["end"]))

    assignments = []
    for slot in sorted(build_slots(), key=difficulty):
        day, start, end = slot["day"], slot["start"], slot["end"]

        if slot["kind"] == "dual":
            minister = pick(candidates("minister", day, start, end))
            hall = pick(candidates("hall", day, start, end))
            for person in (minister, hall):
                if person:
                    key = person_key(person)
                    used.add(key)
                    totals[key] = totals.get(key, 0) + 1
            assignments.append({
                "day": day, "block": slot["block"], "start": start, "end": end,
                "kind": "dual",
                "minister": person_key(minister) if minister else "",
                "hall": person_key(hall) if hall else "",
            })
        else:
            person = pick(candidates("member", day, start, end))
            if person:
                key = person_key(person)
                used.add(key)
                totals[key] = totals.get(key, 0) + 1
            assignments.append({
                "day": day, "block": slot["block"], "start": start, "end": end,
                "kind": "member",
                "person": person_key(person) if person else "",
            })

    return assignments, totals


def swap_candidates(person_dicts, specials, week, groups, role, day, start, end, used_keys):
    """某班次可替换的人（当时有空 + 本周未被排）。"""
    name = (groups or {}).get(role) or ""
    if not name:
        return []
    out = []
    for person in person_dicts or []:
        key = person_key(person)
        if not key or key in used_keys:
            continue
        if (person.get("group") or "") != name:
            continue
        if is_busy(person, specials, key, day, start, end, week):
            continue
        out.append(key)
    return sorted(out)
