import json
import os
import traceback
from datetime import datetime

from .models import Person
from .paths import base_dir, data_dir


def _base_dir() -> str:
    """打包成 exe 后数据放在 exe 同级目录，开发时放在项目根目录。"""
    return base_dir()


_DATA_DIR = data_dir()
DATA_FILE = os.path.join(_DATA_DIR, "people.json")
GROUPS_FILE = os.path.join(_DATA_DIR, "groups.json")


def storage_path() -> str:
    """WebView2 的缓存目录固定放在 data 下，避免随启动方式变化导致启动失败。"""
    path = os.path.join(_DATA_DIR, "webview")
    os.makedirs(path, exist_ok=True)
    return path


def load_people(path: str = DATA_FILE) -> list:
    """从本地 JSON 读取已导入的人员，失败时返回空列表。"""
    return load_data(path)[0]


def load_data(path: str = DATA_FILE) -> tuple:
    """读取人员列表，返回 (people, 文件里记录的识别算法版本)。"""
    if not os.path.exists(path):
        return [], 0
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return [], 0

    if isinstance(data, dict):
        items = data.get("people") or []
    elif isinstance(data, list):
        # 兼容早期直接保存人员数组的格式
        items = data
    else:
        return [], 0

    if not isinstance(items, list):
        return [], 0
    people = [Person.from_dict(item) for item in items if isinstance(item, dict)]
    version = max([person.parsed_with for person in people] or [0])
    return people, version


def _write_log(note: str) -> None:
    """把人员数据的写入/拒绝记录到 data/logs/people-write.log。

    人员数据被抹过一次（52 人 → 空），当时查不出是谁写的，所以留个流水：
    以后再有异常，日志里直接有调用栈，不用靠猜。
    """
    try:
        folder = os.path.join(_DATA_DIR, "logs")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(folder, "people-write.log"), "a", encoding="utf-8") as handle:
            handle.write(f"\n[{stamp}] {note}\n")
    except Exception:  # noqa: BLE001
        pass


def save_people(people: list, path: str = DATA_FILE, allow_empty: bool = False) -> None:
    """把人员列表保存为本地 JSON（每人记录识别算法版本）。

    **默认拒绝把「有内容」的文件写成空列表**。这是刻意的安全阀：
    读失败和"真的一个人都没有"在代码里长得一模一样，只要哪条自动流程
    拿到空列表还照常落盘，用户的数据就一次性没了（真实发生过）。
    确实要清空（用户显式删除最后一个人）时传 allow_empty=True。
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    if not people and not allow_empty:
        existing = load_people(path)
        if existing:
            _write_log(
                f"拒绝写入空列表：{path} 现有 {len(existing)} 人\n"
                + "".join(traceback.format_stack()[-6:])
            )
            return

    payload = {"people": [person.to_dict() for person in people]}
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def load_groups(path: str = GROUPS_FILE) -> list:
    """读取分组名单，返回 [{'name':..., 'parent':...}]；兼容旧的纯字符串格式。"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    groups = []
    for item in data:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            parent = str(item.get("parent") or "").strip()
        else:
            name = str(item).strip()
            parent = ""
        if name:
            groups.append({"name": name, "parent": parent})
    return groups


def save_groups(groups: list, path: str = GROUPS_FILE) -> None:
    """保存分组名单（统一成 {name, parent} 结构）。"""
    normalized = []
    for item in groups:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            parent = str(item.get("parent") or "").strip()
        else:
            name = str(item).strip()
            parent = ""
        if name:
            normalized.append({"name": name, "parent": parent})

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(normalized, stream, ensure_ascii=False, indent=2)
