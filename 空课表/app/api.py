import tkinter as tk
import os
import sys
import threading
from tkinter import filedialog

from .config import app_info
from .parser import PARSER_VERSION, parse_pdf
from .storage import (
    DATA_FILE,
    GROUPS_FILE,
    load_data,
    load_groups,
    load_people,
    save_groups,
    save_people,
)


class Api:
    """暴露给前端 JS 的接口（pywebview 桥）。"""

    def __init__(self, data_path: str = None, groups_path: str = None):
        self._window = None
        self.data_path = data_path or DATA_FILE
        self.groups_path = groups_path or GROUPS_FILE
        self.people, self.parser_version = load_data(self.data_path)
        self.groups = load_groups(self.groups_path)
        self._lock = threading.Lock()
        self._import = self._idle_import_state()

    @staticmethod
    def _idle_import_state() -> dict:
        return {
            "running": False,
            "finished": False,
            "total": 0,
            "done": 0,
            "current": "",
            "added": 0,
            "updated": 0,
            "errors": [],
            "folder": "",
        }

    def get_people(self) -> list:
        return [person.to_dict() for person in self.people]

    def get_app_info(self) -> dict:
        info = app_info()
        info["parser_version"] = PARSER_VERSION
        info["data_version"] = self.parser_version
        # 只要有人是用旧版识别算法导入的，就提示重新识别一次
        info["needs_refresh"] = any(
            (person.parsed_with or 0) < PARSER_VERSION for person in self.people
        )
        return info

    def refresh_schedules(self) -> dict:
        """用当前识别算法重新解析已导入的课表文件，保留分组信息。"""
        refreshed = 0
        missing = []
        errors = []
        for index, person in enumerate(self.people):
            if (person.parsed_with or 0) >= PARSER_VERSION:
                continue
            source = (person.source or "").strip()
            fresh = None
            if source and os.path.exists(source):
                try:
                    fresh = parse_pdf(source)
                except Exception as exc:  # noqa: BLE001
                    errors.append({"path": source, "error": str(exc)})
                    continue
                if not fresh.courses:
                    errors.append({"path": source, "error": "未识别到课表内容"})
                    continue
            else:
                # 老数据没有记录来源文件，去常见目录里按姓名找同名的课表 PDF
                located = self._locate_source(person)
                if located is None:
                    if person.name:
                        missing.append(person.name)
                    continue
                source, fresh = located
            fresh.group = person.group
            fresh.source = source
            fresh.parsed_with = PARSER_VERSION
            self.people[index] = fresh
            refreshed += 1

        self.parser_version = max(
            [person.parsed_with or 0 for person in self.people] or [0]
        )
        save_people(self.people, self.data_path)
        return {
            "people": self.get_people(),
            "groups": self.get_groups(),
            "refreshed": refreshed,
            "missing": missing,
            "errors": errors,
        }

    @staticmethod
    def _search_dirs() -> list:
        """可能存放课表 PDF 的目录（程序目录、上级目录以及其中的「课表」文件夹）。"""
        if getattr(sys, "frozen", False):
            base = os.path.dirname(os.path.abspath(sys.executable))
            roots = [base, os.path.dirname(base)]
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            roots = [base, os.path.dirname(base)]
        dirs = []
        for root in roots:
            for candidate in (root, os.path.join(root, "课表")):
                if candidate and candidate not in dirs:
                    dirs.append(candidate)
        return dirs

    def _locate_source(self, person):
        """按姓名在常见目录里找这个人的课表 PDF，返回 (路径, 解析结果)。"""
        name = (person.name or "").strip()
        if not name:
            return None
        for directory in self._search_dirs():
            if not os.path.isdir(directory):
                continue
            try:
                entries = sorted(os.listdir(directory))
            except OSError:
                continue
            for entry in entries:
                if not entry.lower().endswith(".pdf") or name not in entry:
                    continue
                path = os.path.join(directory, entry)
                try:
                    candidate = parse_pdf(path)
                except Exception:  # noqa: BLE001
                    continue
                if not candidate.courses:
                    continue
                if (
                    person.student_id
                    and candidate.student_id
                    and person.student_id != candidate.student_id
                ):
                    continue
                return path, candidate
        return None

    def get_groups(self) -> list:
        return [dict(group) for group in self.groups]

    def add_group(self, name: str, parent: str = "") -> dict:
        name = (name or "").strip()
        parent = (parent or "").strip()
        if name and not self._has_group(name):
            if parent and not self._has_group(parent):
                parent = ""
            self.groups.append({"name": name, "parent": parent})
            save_groups(self.groups, self.groups_path)
        return {"groups": self.get_groups(), "people": self.get_people()}

    def remove_group(self, name: str) -> dict:
        name = (name or "").strip()
        targets = self._group_tree(name)
        if targets:
            self.groups = [g for g in self.groups if g["name"] not in targets]
            save_groups(self.groups, self.groups_path)
        for person in self.people:
            if person.group in targets:
                person.group = ""
        save_people(self.people, self.data_path)
        return {"groups": self.get_groups(), "people": self.get_people()}

    def reorder_groups(self, order: list) -> dict:
        """按给定的名称顺序重排分组（未列出的分组保持原有相对顺序）。"""
        by_name = {group["name"]: group for group in self.groups}
        new_groups = []
        for name in order or []:
            group = by_name.pop(str(name), None)
            if group is not None:
                new_groups.append(group)
        for group in self.groups:
            if group["name"] in by_name:
                new_groups.append(group)
                by_name.pop(group["name"])
        self.groups = new_groups
        save_groups(self.groups, self.groups_path)
        return {"groups": self.get_groups(), "people": self.get_people()}

    def update_group(self, name: str, new_name: str = "", parent: str = "") -> dict:
        """重命名分组并可选修改上级分组；不允许移动到自己的下级里。"""
        name = (name or "").strip()
        if not name or not self._has_group(name):
            return {"groups": self.get_groups(), "people": self.get_people()}

        new_name = (new_name or "").strip() or name
        parent = (parent or "").strip()
        tree = self._group_tree(name)

        if new_name != name and self._has_group(new_name):
            return {"groups": self.get_groups(), "people": self.get_people()}
        if parent and not self._has_group(parent):
            parent = ""
        if parent in tree:
            parent = ""

        for group in self.groups:
            if group["name"] == name:
                group["name"] = new_name
                group["parent"] = parent
            elif group["parent"] == name:
                group["parent"] = new_name
        for person in self.people:
            if person.group == name:
                person.group = new_name

        save_groups(self.groups, self.groups_path)
        save_people(self.people, self.data_path)
        return {"groups": self.get_groups(), "people": self.get_people()}

    def _has_group(self, name: str) -> bool:
        return any(group["name"] == name for group in self.groups)

    def _group_tree(self, root: str) -> set:
        """返回 root 及其所有下级分组的名字集合。"""
        targets = {root}
        changed = True
        while changed:
            changed = False
            for group in self.groups:
                if group["parent"] in targets and group["name"] not in targets:
                    targets.add(group["name"])
                    changed = True
        return targets

    def import_schedules(self, group: str = "") -> dict:
        """选择文件后立即导入（同步版，供测试与兼容使用）。"""
        group = (group or "").strip()
        paths = self._ask_pdf_files()
        self._run_import(paths, group)
        return {
            "people": self.get_people(),
            "groups": self.get_groups(),
            "errors": self.get_import_status()["errors"],
        }

    def start_import(self, kind: str = "files", group: str = "") -> dict:
        """在后台线程里导入课表，前端轮询 get_import_status() 显示进度。

        kind 取 "files"（选择若干 PDF）或 "folder"（选择文件夹，递归导入里面的 PDF）。
        """
        group = (group or "").strip()
        with self._lock:
            if self._import.get("running"):
                return {"started": False, "busy": True}

        if kind == "folder":
            folder = self._ask_pdf_folder()
            if not folder:
                return {"started": False, "cancelled": True}
            paths = self._list_folder_pdfs(folder)
        else:
            folder = ""
            paths = self._ask_pdf_files()
            if not paths:
                return {"started": False, "cancelled": True}

        with self._lock:
            self._import = self._idle_import_state()
            self._import.update(
                {"running": True, "total": len(paths), "folder": folder}
            )

        worker = threading.Thread(
            target=self._run_import, args=(paths, group), daemon=True
        )
        worker.start()
        return {
            "started": True,
            "total": len(paths),
            "folder": folder,
        }

    def get_import_status(self) -> dict:
        with self._lock:
            return dict(self._import)

    def _run_import(self, paths: list, group: str) -> None:
        """逐个识别并合并课表，过程中更新进度。"""
        errors = []
        added = updated = 0
        total = len(paths)
        for index, path in enumerate(paths, 1):
            name = os.path.basename(path)
            with self._lock:
                self._import["current"] = name
            try:
                person = self._load_person(path, group)
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": path, "error": str(exc)})
            else:
                existed = any(self._same_person(p, person) for p in self.people)
                self._merge(person)
                if existed:
                    updated += 1
                else:
                    added += 1
            with self._lock:
                self._import["done"] = index
                self._import["added"] = added
                self._import["updated"] = updated
                self._import["errors"] = list(errors)
            if index % 25 == 0:
                # 人数多时中途落盘，避免程序意外关闭导致整批导入丢失
                save_people(self.people, self.data_path)

        save_people(self.people, self.data_path)
        if group and not self._has_group(group):
            self.groups.append({"name": group, "parent": ""})
            save_groups(self.groups, self.groups_path)
        with self._lock:
            self._import["running"] = False
            self._import["finished"] = True
            self._import["current"] = ""

    @staticmethod
    def _list_folder_pdfs(folder: str) -> list:
        """列出文件夹（含子文件夹）里的 PDF，按路径排序。"""
        found = []
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                if name.lower().endswith(".pdf"):
                    found.append(os.path.join(root, name))
        return found

    @staticmethod
    def _load_person(path: str, group: str):
        person = parse_pdf(path)
        if not person.courses:
            raise ValueError("未识别到课表内容，可能不是课表 PDF")
        person.group = group
        person.source = path
        person.parsed_with = PARSER_VERSION
        return person

    def set_groups(self, assignments: list) -> dict:
        """批量设置分组。assignments 为 [{index, group}, ...]。"""
        for item in assignments or []:
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(self.people):
                group = (item.get("group") or "").strip()
                self.people[index].group = group
                if group and not self._has_group(group):
                    self.groups.append({"name": group, "parent": ""})
        save_people(self.people, self.data_path)
        save_groups(self.groups, self.groups_path)
        return {"groups": self.get_groups(), "people": self.get_people()}

    def remove_people(self, indices: list) -> dict:
        """按序号删除人员（序号为当前人员列表中的下标）。"""
        targets = set()
        for item in indices or []:
            try:
                targets.add(int(item))
            except (TypeError, ValueError):
                continue
        if targets:
            self.people = [p for i, p in enumerate(self.people) if i not in targets]
            save_people(self.people, self.data_path)
        return {"groups": self.get_groups(), "people": self.get_people()}

    @staticmethod
    def _same_person(existing, person) -> bool:
        """同一人判定：优先用学号（唯一），没有学号时才退回姓名。"""
        if existing.semester != person.semester:
            return False
        if existing.student_id and person.student_id:
            return existing.student_id == person.student_id
        return existing.name == person.name

    def _merge(self, person) -> None:
        """同一人用新数据覆盖，否则追加；未指定分组时保留原有分组。"""
        for index, existing in enumerate(self.people):
            if self._same_person(existing, person):
                if not person.group and existing.group:
                    person.group = existing.group
                self.people[index] = person
                return
        self.people.append(person)

    @staticmethod
    def _ask_pdf_files() -> list:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            paths = filedialog.askopenfilenames(
                parent=root,
                title="选择课表 PDF 文件",
                filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            )
            return list(paths)
        finally:
            root.destroy()

    @staticmethod
    def _ask_pdf_folder() -> str:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            folder = filedialog.askdirectory(
                parent=root,
                title="选择存有课表 PDF 的文件夹",
            )
            return folder or ""
        finally:
            root.destroy()

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def maximize(self) -> None:
        if self._window is not None:
            self._window.maximize()

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()
