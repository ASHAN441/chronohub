"""手动添加的「特殊安排」的存取。

为什么需要：课表 PDF 只能反映排课结果。现实里还有课表体现不了的情况——
部门例会、临时值班、校运会、老师调课停课。用户统计完之后需要把这些补进去，
否则算出来的空课表是错的。

设计取舍
--------
- **只做「占用」一个方向**：手动条目一律按「这段没空」处理，和用户的诉求一致。
  如果以后需要「课表有课但其实有空」，再加 type 字段即可，旧数据仍然兼容。
- **人员用稳定 key**：优先学号，没有学号才退回姓名。不能用人员列表下标——
  导入/删除会让下标整体位移，存下来的条目会指错人。
- 存成独立的 `data/specials.json`，不动 people.json，避免和课表解析互相干扰。
"""

from __future__ import annotations

import json
import os
import uuid

from .paths import data_dir

SPECIALS_FILE = os.path.join(data_dir(), "specials.json")

MAX_PERIOD = 11
MAX_WEEK = 18


def person_key(person: dict) -> str:
    """人员稳定标识：优先学号，其次姓名。"""
    student_id = str(person.get("student_id") or "").strip()
    if student_id:
        return student_id
    return str(person.get("name") or "").strip()


def _clean_ints(values, low: int, high: int) -> list:
    out = []
    for value in values or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if low <= number <= high and number not in out:
            out.append(number)
    return sorted(out)


def normalize(entry) -> dict | None:
    """校验并规整一条手动安排；字段不合法时返回 None。"""
    if not isinstance(entry, dict):
        return None

    who = []
    for item in entry.get("who") or []:
        key = str(item or "").strip()
        if key and key not in who:
            who.append(key)

    days = _clean_ints(entry.get("days"), 1, 7)
    periods = _clean_ints(entry.get("periods"), 1, MAX_PERIOD)
    weeks = _clean_ints(entry.get("weeks"), 1, MAX_WEEK)

    if not who or not days or not periods or not weeks:
        return None

    return {
        "id": str(entry.get("id") or uuid.uuid4().hex[:10]),
        "who": who,
        "days": days,
        "periods": periods,
        "weeks": weeks,
    }


def load_specials(path: str = None) -> list:
    """读取手动安排。文件损坏时返回空列表，不让应用起不来。"""
    target = path or SPECIALS_FILE
    if not os.path.exists(target):
        return []
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return []

    if isinstance(data, dict):
        items = data.get("specials") or []
    elif isinstance(data, list):
        items = data
    else:
        return []

    result = []
    for item in items:
        cleaned = normalize(item)
        if cleaned is not None:
            result.append(cleaned)
    return result


def save_specials(entries: list, path: str = None) -> None:
    """写入手动安排。"""
    target = path or SPECIALS_FILE
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {"specials": [e for e in (normalize(x) for x in entries) if e]}
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
