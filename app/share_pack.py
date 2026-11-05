"""数据包：把一个完整的「课表 + 分组 + 特殊安排 + 值班表」打成单个 JSON 文件。

用途：拷到另一台电脑，在新设备上导入，就能看到同样的数据。

**不含账号密码**。教务账号用 Windows DPAPI 加密，绑定本机，拷过去也解不开，
所以干脆不放进包里——这样文件也不会携带任何可被滥用的凭据。

为什么是单个 JSON 而不是 zip：
这四个数据文件加起来只有几百 KB，压缩省不了多少；JSON 能直接用记事本打开、
能用 git 管、出问题能一眼看出是哪个字段坏了。真需要压缩，用户自己右键压缩即可。
"""

from __future__ import annotations

import json
from datetime import datetime

FORMAT = "chronohub-share"
FORMAT_VERSION = 1

# 包里包含的五个数据块。改这个列表要同时改 _empty_data() 和导出的调用方。
DATA_KEYS = ("people", "groups", "specials", "rosters", "adjustments")

_LABELS = {
    "people": "人员课表",
    "groups": "分组",
    "specials": "特殊安排",
    "rosters": "值班表",
    "adjustments": "次数调整",
}


def _empty_data() -> dict:
    return {key: [] for key in DATA_KEYS}


def build(
    people: list,
    groups: list,
    specials: list,
    rosters: list,
    adjustments: list,
    app_version: str = "",
    exported_at: datetime | None = None,
) -> dict:
    """组装一个数据包。入参都是普通 dict 列表（已经清理过的）。"""
    data = {
        "people": list(people or []),
        "groups": list(groups or []),
        "specials": list(specials or []),
        "rosters": list(rosters or []),
        "adjustments": list(adjustments or []),
    }
    stamp = (exported_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "exported_at": stamp,
        "summary": summarize_data(data),
        "data": data,
    }


def dumps(package: dict) -> str:
    """序列化。indent=1 让文件还能用记事本看清结构，体积代价很小。"""
    return json.dumps(package, ensure_ascii=False, indent=1)


def summarize_data(data: dict) -> dict:
    return {
        "people": len(data.get("people") or []),
        "courses": sum(len(p.get("courses") or []) for p in data.get("people") or []),
        "groups": len(data.get("groups") or []),
        "specials": len(data.get("specials") or []),
        "rosters": len(data.get("rosters") or []),
        "roster_weeks": [r.get("week") for r in data.get("rosters") or []],
        "adjustments": len(data.get("adjustments") or []),
    }


def loads(text: str) -> tuple[dict | None, str]:
    """解析并校验。返回 (数据包, 错误说明)；出错时数据包为 None。

    校验刻意宽松（不逐字段查类型），真正的清洗交给各个 store 的
    load_people / load_rosters 等函数——它们本来就会丢弃非法条目。
    这里只挡住「根本不是数据包」「版本比本程序新」这两类问题。
    """
    if not text or not text.strip():
        return None, "文件是空的"

    try:
        package = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"不是有效的 JSON 文件（{exc.msg}，第 {exc.lineno} 行）"
    except UnicodeDecodeError:
        return None, "文件编码不是 UTF-8，可能选错了文件"

    if not isinstance(package, dict):
        return None, "文件内容不是一个数据包"

    if package.get("format") != FORMAT:
        return None, "这不是 ChronoHub 数据包（缺少 format 标记）"

    version = package.get("format_version")
    if not isinstance(version, int):
        return None, "数据包缺少 format_version"
    if version > FORMAT_VERSION:
        return None, (
            f"这个数据包来自更新的版本（格式 v{version}），"
            f"当前程序只认到 v{FORMAT_VERSION}，请先升级软件"
        )

    data = package.get("data")
    if not isinstance(data, dict):
        return None, "数据包里没有 data 段"

    cleaned = {}
    for key in DATA_KEYS:
        value = data.get(key)
        if value is None:
            cleaned[key] = []
        elif isinstance(value, list):
            cleaned[key] = value
        else:
            return None, f"数据包里的 {key} 不是列表"

    package["data"] = cleaned
    package["summary"] = summarize_data(cleaned)
    return package, ""


def describe(summary: dict) -> str:
    """一句话描述包内容，给确认弹窗用。"""
    parts = [f"{summary.get('people', 0)} 人（{summary.get('courses', 0)} 门课）"]
    if summary.get("groups"):
        parts.append(f"{summary['groups']} 个分组")
    if summary.get("specials"):
        parts.append(f"{summary['specials']} 条特殊安排")
    weeks = summary.get("roster_weeks") or []
    if weeks:
        parts.append(f"{len(weeks)} 张值班表（第 {'、'.join(str(w) for w in weeks)} 周）")
    elif summary.get("rosters"):
        parts.append(f"{summary['rosters']} 张值班表")
    return " · ".join(parts)


def default_filename(now: datetime | None = None) -> str:
    return "ChronoHub数据-" + (now or datetime.now()).strftime("%Y%m%d-%H%M") + ".json"
