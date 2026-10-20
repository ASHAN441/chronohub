"""值班表的存取。

一张值班表 = 某一周的全部班次安排。按周存，重新生成同一周就覆盖。

为什么按周存而不是按学期存：
- 课表按周次变化（单双周、某几周才有的课），每周的可用人不同
- 重新抽一次只影响这一周，不会打乱其他周
- 「值了几次班」= 所有已存值班表的汇总，永远和实际排班一致

人员一律用稳定 key（学号优先，其次姓名），理由同 specials_store：
导入/删除人员会让列表下标位移，存下标会指错人。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from .paths import data_dir

DUTY_FILE = os.path.join(data_dir(), "duty.json")


def person_key(person: dict) -> str:
    """人员稳定标识：优先学号，其次姓名。"""
    student_id = str(person.get("student_id") or "").strip()
    if student_id:
        return student_id
    return str(person.get("name") or "").strip()


def _clean_assignment(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    try:
        day = int(item.get("day"))
        start = int(item.get("start"))
        end = int(item.get("end"))
        block = int(item.get("block"))
    except (TypeError, ValueError):
        return None
    if not (1 <= day <= 7 and 1 <= start <= end <= 11):
        return None

    if kind == "dual":
        minister = str(item.get("minister") or "").strip()
        hall = str(item.get("hall") or "").strip()
        return {
            "day": day,
            "block": block,
            "start": start,
            "end": end,
            "kind": "dual",
            "minister": minister,
            "hall": hall,
        }

    person = str(item.get("person") or "").strip()
    return {
        "day": day,
        "block": block,
        "start": start,
        "end": end,
        "kind": "member",
        "person": person,
    }


def normalize_roster(data) -> dict | None:
    """校验并规整一张值班表。"""
    if not isinstance(data, dict):
        return None
    try:
        week = int(data.get("week"))
    except (TypeError, ValueError):
        return None
    if not (1 <= week <= 30):
        return None

    assignments = []
    for item in data.get("assignments") or []:
        cleaned = _clean_assignment(item)
        if cleaned is not None:
            assignments.append(cleaned)
    if not assignments:
        return None

    groups = data.get("groups") or {}
    return {
        "id": str(data.get("id") or uuid.uuid4().hex[:10]),
        "week": week,
        "groups": {
            "member": str(groups.get("member") or ""),
            "minister": str(groups.get("minister") or ""),
            "hall": str(groups.get("hall") or ""),
        },
        "assignments": assignments,
        "created_at": str(data.get("created_at") or datetime.now().isoformat(timespec="seconds")),
    }


def load_rosters(path: str = None) -> list:
    """读取全部值班表。文件损坏返回空列表，不让应用起不来。"""
    target = path or DUTY_FILE
    if not os.path.exists(target):
        return []
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return []

    items = data.get("rosters") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        cleaned = normalize_roster(item)
        if cleaned is not None:
            result.append(cleaned)
    return sorted(result, key=lambda r: r["week"])


def save_rosters(rosters: list, path: str = None) -> None:
    target = path or DUTY_FILE
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {"rosters": [r for r in (normalize_roster(x) for x in rosters) if r]}
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def upsert_roster(rosters: list, roster: dict) -> list:
    """同一周只保留一张表：新的替换旧的。"""
    week = roster["week"]
    kept = [r for r in rosters if r["week"] != week]
    kept.append(roster)
    return sorted(kept, key=lambda r: r["week"])


def count_assignments(rosters: list, exclude_week: int = None) -> dict:
    """汇总每个人值了几次班。

    exclude_week 用来排除「当前正在生成的那一周」——否则重新生成同一周时，
    已经排进去的人会被当成"值过班"，导致他本周轮空，次数反而越排越偏。
    """
    counts = {}
    for roster in rosters:
        if exclude_week is not None and roster["week"] == exclude_week:
            continue
        for item in roster["assignments"]:
            if item["kind"] == "dual":
                keys = [item.get("minister"), item.get("hall")]
            else:
                keys = [item.get("person")]
            for key in keys:
                key = str(key or "").strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
    return counts


# ================= 手动调整值班次数 =================
#
# 为什么要单独存一个文件：自动排班和手动修正是两个生命周期。
# 混在 duty.json 里，重新生成某一周或换人时容易被覆盖掉；
# 分开存就永远不会被排班动作碰到。
#
# 存成「流水」而不是「最终数字」：每次 +1/-1 都留一条记录（谁、多少、什么时候、
# 为什么），既能追溯，也能单条撤销。最终次数 = 排班统计 + 流水合计。

ADJUST_FILE = os.path.join(data_dir(), "duty_adjust.json")


def _clean_adjustment(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    person = str(item.get("person") or "").strip()
    if not person:
        return None
    try:
        delta = int(item.get("delta"))
    except (TypeError, ValueError):
        return None
    if delta == 0 or not (-99 <= delta <= 99):
        return None
    return {
        "id": str(item.get("id") or uuid.uuid4().hex[:10]),
        "person": person,
        "delta": delta,
        "note": str(item.get("note") or "").strip()[:60],
        "at": str(item.get("at") or datetime.now().isoformat(timespec="seconds")),
    }


def load_adjustments(path: str = None) -> list:
    """读取手动调整流水。文件损坏返回空列表。"""
    target = path or ADJUST_FILE
    if not os.path.exists(target):
        return []
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return []

    items = data.get("adjustments") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        cleaned = _clean_adjustment(item)
        if cleaned is not None:
            result.append(cleaned)
    return result


def save_adjustments(items: list, path: str = None) -> None:
    target = path or ADJUST_FILE
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {"adjustments": [a for a in (_clean_adjustment(x) for x in items) if a]}
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def sum_adjustments(items: list) -> dict:
    """每人手动调整的净次数。"""
    totals = {}
    for item in items or []:
        person = str(item.get("person") or "").strip()
        if not person:
            continue
        try:
            delta = int(item.get("delta") or 0)
        except (TypeError, ValueError):
            continue
        totals[person] = totals.get(person, 0) + delta
    return totals
