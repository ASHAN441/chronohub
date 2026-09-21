import json
import os

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


def save_people(people: list, path: str = DATA_FILE) -> None:
    """把人员列表保存为本地 JSON（每人记录识别算法版本）。"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
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
