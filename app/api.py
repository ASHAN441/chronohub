import json
import tkinter as tk
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

from .account_service import AccountService
from .clipboard import set_text_temporary
from .config import app_info
from .docx_export import export_roster, template_path
from .duty_scheduler import generate_roster, is_busy, swap_candidates
from .duty_store import (
    ADJUST_FILE,
    DUTY_FILE,
    _clean_adjustment as clean_adjustment,
    count_assignments,
    load_adjustments,
    load_rosters,
    normalize_roster,
    person_key as duty_person_key,
    save_adjustments,
    save_rosters,
    sum_adjustments,
    upsert_roster,
)
from .parser import PARSER_VERSION, parse_pdf
from .paths import base_dir, sub_dir
from .people_store import (
    DATA_FILE,
    GROUPS_FILE,
    load_data,
    load_groups,
    load_people,
    save_groups,
    save_people,
)
from .qtbridge import QtBridge, clear_last_error, last_error
from .specials_store import (
    SPECIALS_FILE,
    load_specials,
    normalize as normalize_special,
    save_specials,
)
from .system_urls import DEFAULT_LOGIN_URL, SYSTEM_URLS, normalize_login_url


# 无边框窗口的可拖拽边缘 → 组合成 Qt 的 Edges
_RESIZE_EDGES = {
    "left": ("left",), "right": ("right",), "top": ("top",), "bottom": ("bottom",),
    "top-left": ("top", "left"), "top-right": ("top", "right"),
    "bottom-left": ("bottom", "left"), "bottom-right": ("bottom", "right"),
}


def _default_save_dir() -> str:
    """保存对话框的默认目录：桌面优先，其次用户主目录。

    不指定的话，从快捷方式启动时对话框可能落在 C:\\Windows\\System32 这类
    不可写的地方，用户随手一存就撞权限错误。
    """
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    return desktop if os.path.isdir(desktop) else home


class Api:
    """暴露给前端 JS 的接口（pywebview 桥）。"""

    def __init__(
        self,
        data_path: str = None,
        groups_path: str = None,
        accounts_db_path: str = None,
        specials_path: str = None,
        duty_path: str = None,
        adjust_path: str = None,
    ):
        self._window = None
        # 无边框窗口：自己记最大化状态（pywebview 不暴露 isMaximized，
        # 读 Qt 要跨线程往返，没那个必要——状态只由 toggle_max 改）
        self._maximized = False
        self._qt_app = None
        self._bridge = None
        self._browser = None
        self.data_path = data_path or DATA_FILE
        self.groups_path = groups_path or GROUPS_FILE
        # specials / duty 默认跟 people 放在同一个目录：这样注入 data_path 的测试
        # 和自定义数据目录会自动隔离，不会去读写真实数据。
        if specials_path:
            self.specials_path = specials_path
        elif data_path:
            self.specials_path = os.path.join(
                os.path.dirname(os.path.abspath(data_path)), "specials.json"
            )
        else:
            self.specials_path = SPECIALS_FILE

        if duty_path:
            self.duty_path = duty_path
        elif data_path:
            self.duty_path = os.path.join(
                os.path.dirname(os.path.abspath(data_path)), "duty.json"
            )
        else:
            self.duty_path = DUTY_FILE

        # 手动调整值班次数：单独一个文件，不会被排班动作覆盖
        if adjust_path:
            self.adjust_path = adjust_path
        elif data_path:
            self.adjust_path = os.path.join(
                os.path.dirname(os.path.abspath(data_path)), "duty_adjust.json"
            )
        else:
            self.adjust_path = ADJUST_FILE

        # 导出的兜底目录：跟数据放一起，注入 data_path 时自动隔离
        if data_path:
            self.exports_dir = os.path.join(
                os.path.dirname(os.path.abspath(data_path)), "exports"
            )
        else:
            self.exports_dir = sub_dir("exports")

        self.people, self.parser_version = load_data(self.data_path)
        self.groups = load_groups(self.groups_path)
        self.specials = load_specials(self.specials_path)
        self.rosters = load_rosters(self.duty_path)
        self.adjustments = load_adjustments(self.adjust_path)
        self._lock = threading.Lock()
        # 人员数据的读写锁。为什么必须有：批量导入跑在后台线程，而
        # 「导出课表 → 自动入库」走 js_api 子线程，两者可能同时 _merge 并写同一个
        # people.json —— 并发写会让 JSON 文件损坏。所有人员变更都必须走这个锁。
        self._people_lock = threading.RLock()
        self._import = self._idle_import_state()
        # 账号模块（来自原「教务系统管理器」）；db 路径可注入，便于测试
        self.accounts = AccountService(accounts_db_path)

    def attach_qt(self, qt_app) -> None:
        """由 app.main 在 GUI 启动后注入 Qt QApplication 实例。

        注入后建立通往 Qt 主线程的投递桥，并在主线程里创建内嵌浏览器控制器
        （QTimer 等必须在有事件循环的线程里创建）。
        """
        self._qt_app = qt_app
        self._bridge = QtBridge(qt_app)
        self._bridge.run(self._create_browser)

    def _create_browser(self) -> None:
        """在 Qt 主线程里创建内嵌浏览器。延迟导入，避免无谓加载 QtWebEngine。"""
        from .browser import EmbeddedBrowser

        if self._browser is None:
            self._browser = EmbeddedBrowser()

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
        with self._people_lock:
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
            with self._people_lock:
                self.people[index] = fresh
            refreshed += 1

        self.parser_version = max(
            [person.parsed_with or 0 for person in self.people] or [0]
        )
        with self._people_lock:
            # 这个方法是自动触发的（前端渲染后会调一次）。如果内存里没有人
            # （启动时文件正被占用、读到半截 JSON 等），回写就会把磁盘上的
            # 人员一次性抹掉——这是不可逆的数据毁灭，绝不能发生。
            if self.people:
                self._save_people()
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
        base = base_dir()
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
                if self._merge_and_save(person):
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
                with self._people_lock:
                    self._save_people()

        with self._people_lock:
            self._save_people()
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
        with self._people_lock:
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
            self._save_people()
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
            with self._people_lock:
                self.people = [p for i, p in enumerate(self.people) if i not in targets]
                self._save_people(allow_empty=True)
        return {"groups": self.get_groups(), "people": self.get_people()}

    # ================= 手动添加的特殊安排 =================
    #
    # 课表 PDF 体现不了的情况（例会、值班、调课停课）由用户手动补进来，
    # 统计时按「没空」处理。存独立的 specials.json，不动 people.json。

    def get_specials(self) -> dict:
        with self._people_lock:
            return {"specials": [dict(item) for item in self.specials]}

    def add_special(self, who, days, periods, weeks) -> dict:
        entry = normalize_special(
            {"who": who, "days": days, "periods": periods, "weeks": weeks}
        )
        if entry is None:
            return {
                "ok": False,
                "error": "人名、星期、节次、周次都不能为空",
                "specials": self.get_specials()["specials"],
            }
        with self._people_lock:
            self.specials.append(entry)
            save_specials(self.specials, self.specials_path)
            snapshot = [dict(item) for item in self.specials]
        return {"ok": True, "specials": snapshot}

    def remove_special(self, special_id) -> dict:
        target = str(special_id or "")
        with self._people_lock:
            before = len(self.specials)
            self.specials = [s for s in self.specials if s["id"] != target]
            if len(self.specials) != before:
                save_specials(self.specials, self.specials_path)
            snapshot = [dict(item) for item in self.specials]
        return {"ok": True, "specials": snapshot}

    # ================= 值班表 =================
    #
    # 按周存：一张表 = 某一周的全部班次。重新生成同一周会覆盖。
    # 「值了几次班」= 所有已存值班表的汇总，永远和实际排班一致。

    def _group_scope(self, name: str) -> set:
        """选中的分组 → 它自己 + 所有下级分组。

        分组是树形的（部员 → 技术传媒中心 …），排班要支持「选父级 = 整棵树」，
        否则像「部员」这种自己名下没人、只当容器的分组，在界面上根本没法选。
        """
        name = (name or "").strip()
        if not name:
            return set()
        scope = {name}
        changed = True
        while changed:
            changed = False
            for group in self.groups or []:
                gname = (group.get("name") or "").strip()
                parent = (group.get("parent") or "").strip()
                if gname and parent and parent in scope and gname not in scope:
                    scope.add(gname)
                    changed = True
        return scope

    def _group_tree(self, group_of: dict) -> list:
        """分组层级列表，给排班的分组下拉用。

        total 是「整棵子树」的人数——直接告诉用户选这一项会影响多少人。
        还带上了「人员分组里有、但分组树里没有」的历史数据，避免选不到人。
        """
        direct = {}
        for group_name in (group_of or {}).values():
            if group_name:
                direct[group_name] = direct.get(group_name, 0) + 1

        tree = []
        seen = set()

        def walk(parent, depth):
            for group in self.groups or []:
                if (group.get("parent") or "").strip() != parent:
                    continue
                name = (group.get("name") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                total = sum(direct.get(g, 0) for g in self._group_scope(name))
                tree.append({
                    "name": name, "parent": parent, "depth": depth,
                    "direct": direct.get(name, 0), "total": total,
                })
                walk(name, depth + 1)

        walk("", 0)
        for name, count in direct.items():
            if name not in seen and name != "未分组":
                tree.append({"name": name, "parent": "", "depth": 0,
                             "direct": count, "total": count})
        return tree

    def get_duty(self) -> dict:
        with self._people_lock:
            rosters = [json.loads(json.dumps(r)) for r in self.rosters]
            adjustments = [dict(item) for item in self.adjustments]
            people = [p.to_dict() for p in self.people]

        names = {}
        group_of = {}
        for person in people:
            key = duty_person_key(person)
            if key:
                names[key] = person.get("name") or key
                group_of[key] = person.get("group") or ""

        roster_counts = count_assignments(rosters)
        manual_counts = sum_adjustments(adjustments)

        # 合计 = 排班统计 + 手动调整，前端只认 counts
        counts = dict(roster_counts)
        for key, delta in manual_counts.items():
            counts[key] = counts.get(key, 0) + delta

        return {
            "rosters": rosters,
            "counts": counts,
            "roster_counts": roster_counts,
            "manual_counts": manual_counts,
            "adjustments": adjustments,
            "names": names,
            "groups": group_of,
            "group_names": sorted({g for g in group_of.values() if g}),
            "group_tree": self._group_tree(group_of),
            "has_template": bool(template_path()),
        }

    def adjust_duty(self, person_key, delta, note="") -> dict:
        """手动修改某人的累计值班次数（记一条流水，不碰排班）。"""
        person_key = str(person_key or "").strip()
        if not person_key:
            return {"ok": False, "error": "缺少人员"}
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            return {"ok": False, "error": "调整值必须是整数"}
        if delta == 0:
            return {"ok": False, "error": "调整值不能为 0"}
        if not -99 <= delta <= 99:
            return {"ok": False, "error": "单次调整幅度太大"}

        with self._people_lock:
            known = {duty_person_key(p.to_dict()) for p in self.people}
            if person_key not in known:
                return {"ok": False, "error": "没有这个人"}

            entry = clean_adjustment({"person": person_key, "delta": delta, "note": note})
            if entry is None:
                return {"ok": False, "error": "调整参数不合法"}
            self.adjustments.append(entry)
            save_adjustments(self.adjustments, self.adjust_path)

        payload = self.get_duty()
        payload["ok"] = True
        return payload

    def undo_adjustment(self, adjust_id) -> dict:
        """撤销一条手动调整（按流水 id）。"""
        target = str(adjust_id or "")
        with self._people_lock:
            before = len(self.adjustments)
            self.adjustments = [a for a in self.adjustments if a["id"] != target]
            if len(self.adjustments) != before:
                save_adjustments(self.adjustments, self.adjust_path)
            else:
                return {"ok": False, "error": "没找到这条记录"}

        payload = self.get_duty()
        payload["ok"] = True
        return payload

    def generate_duty(self, week, groups) -> dict:
        try:
            week = int(week)
        except (TypeError, ValueError):
            return {"ok": False, "error": "周次不合法"}

        with self._people_lock:
            people = [p.to_dict() for p in self.people]
            specials = [dict(s) for s in self.specials]
            existing = [json.loads(json.dumps(r)) for r in self.rosters]

        if not people:
            return {"ok": False, "error": "还没有导入课表，无法排班"}

        # 排除当前这一周的历史计数：重排同一周时不该让已排的人轮空
        totals = count_assignments(existing, exclude_week=week)
        # 选父级分组 = 它下面所有分组一起参与排班
        scope = {
            role: self._group_scope((groups or {}).get(role) or "")
            for role in ("member", "minister")
        }
        assignments, _ = generate_roster(people, specials, week, scope, totals)

        # 「本周这个时段不用人值班」是用户手动标的，重排这一周时要保住，
        # 并且被标记的班次上不排人（否则会出现"有人值班但标了不用"）。
        off = []
        for item in existing:
            if item["week"] == week:
                off = [list(pair) for pair in (item.get("off") or [])]
                break
        off_set = {(pair[0], pair[1]) for pair in off}
        for item in assignments:
            if (item["day"], item["block"]) in off_set:
                item["person"] = ""

        roster = normalize_roster({
            "week": week,
            "groups": groups or {},
            "assignments": assignments,
            "off": off,
        })
        if roster is None:
            return {"ok": False, "error": "排班结果为空"}

        with self._people_lock:
            self.rosters = upsert_roster(self.rosters, roster)
            save_rosters(self.rosters, self.duty_path)

        payload = self.get_duty()
        payload["ok"] = True
        payload["week"] = week
        return payload

    def duty_candidates(self, week, day, block, role) -> dict:
        """某班次当前可换的人（当时有空 + 本周未排）。"""
        try:
            week, day, block = int(week), int(day), int(block)
        except (TypeError, ValueError):
            return {"candidates": []}

        roster = None
        with self._people_lock:
            for item in self.rosters:
                if item["week"] == week:
                    roster = json.loads(json.dumps(item))
                    break
            people = [p.to_dict() for p in self.people]
            specials = [dict(s) for s in self.specials]

        if roster is None:
            return {"candidates": []}

        used = set()
        for item in roster["assignments"]:
            if item.get("person"):
                used.add(item["person"])

        slot = None
        for item in roster["assignments"]:
            if item["day"] == day and item["block"] == block:
                slot = item
                break
        if slot is None:
            return {"candidates": []}

        selected = (roster.get("groups") or {}).get(role) or ""
        scope = {role: self._group_scope(selected)}
        keys = swap_candidates(
            people, specials, week, scope,
            role, slot["day"], slot["start"], slot["end"], used,
        )
        names = {}
        for person in people:
            key = duty_person_key(person)
            if key:
                names[key] = person.get("name") or key
        return {"candidates": [{"key": k, "name": names.get(k, k)} for k in keys]}

    def set_duty_off(self, week, day, block, off=True) -> dict:
        """把某个班次标成「本周不用人值班」，或取消这个标记。

        用途：放假、场地检修、那节课本来就没人用……这类情况不该算"缺人"。
        标记存在排班表的 off 列表里，和谁值班无关，所以重排时也能保留。
        """
        try:
            week, day, block = int(week), int(day), int(block)
        except (TypeError, ValueError):
            return {"ok": False, "error": "参数不合法"}
        if not (1 <= day <= 7 and 0 <= block <= 4):
            return {"ok": False, "error": "班次不合理"}

        with self._people_lock:
            index = None
            for i, item in enumerate(self.rosters):
                if item["week"] == week:
                    index = i
                    break
            if index is None:
                return {"ok": False, "error": "这一周还没排班"}

            roster = json.loads(json.dumps(self.rosters[index]))
            pairs = [list(pair) for pair in (roster.get("off") or [])]
            pair = [day, block]

            if off:
                if pair not in pairs:
                    pairs.append(pair)
                # 标了不用人，就把这人撤下来，避免"有人值班"和"不用人"同时成立
                for item in roster.get("assignments") or []:
                    if item.get("day") == day and item.get("block") == block:
                        item["person"] = ""
            else:
                pairs = [p for p in pairs if p != pair]

            roster["off"] = pairs
            cleaned = normalize_roster(roster)
            if cleaned is None:
                return {"ok": False, "error": "排班数据异常"}
            self.rosters[index] = cleaned
            save_rosters(self.rosters, self.duty_path)

        payload = self.get_duty()
        payload["ok"] = True
        payload["week"] = week
        return payload

    def swap_duty(self, week, day, block, role, person_key) -> dict:
        try:
            week, day, block = int(week), int(day), int(block)
        except (TypeError, ValueError):
            return {"ok": False, "error": "参数不合法"}

        person_key = str(person_key or "").strip()
        if not person_key:
            return {"ok": False, "error": "没有选择替换的人"}

        with self._people_lock:
            index = None
            for i, item in enumerate(self.rosters):
                if item["week"] == week:
                    index = i
                    break
            if index is None:
                return {"ok": False, "error": "这一周还没有排班"}

            roster = json.loads(json.dumps(self.rosters[index]))
            slot = None
            for item in roster["assignments"]:
                if item["day"] == day and item["block"] == block:
                    slot = item
                    break
            if slot is None:
                return {"ok": False, "error": "没找到这个班次"}

            # 9-10 节是部长岗，其余是部员岗；台务不再参与排班
            expected = "minister" if slot["kind"] == "minister" else "member"
            if role != expected:
                return {
                    "ok": False,
                    "error": "这个班次只能换部长" if expected == "minister" else "这个班次只能换部员",
                }

            # ---- 服务端校验：前端只给合法候选，但接口本身必须设防 ----
            # 不校验的话，一次并发/过期请求就能把「当时有课的人」或
            # 「本周已经值过的人」写进值班表，破坏每周 1 次的规则。
            people = [p.to_dict() for p in self.people]
            specials = [dict(s) for s in self.specials]

            target = None
            for person in people:
                if duty_person_key(person) == person_key:
                    target = person
                    break
            if target is None:
                return {"ok": False, "error": "没有这个人"}

            role_group = (roster.get("groups") or {}).get(expected)
            if role_group and (target.get("group") or "") not in self._group_scope(role_group):
                return {"ok": False, "error": f"这个人不在「{role_group}」分组里"}

            if is_busy(target, specials, person_key, day, slot["start"], slot["end"], week):
                return {"ok": False, "error": "这个人该时段没空（有课或手动标了忙）"}

            for item in roster["assignments"]:
                if item["day"] == day and item["block"] == block:
                    continue  # 正在被替换的这个班次不算
                if item.get("person") == person_key:
                    return {"ok": False, "error": "这个人本周已经排过班（每周最多 1 次）"}

            slot["person"] = person_key
            roster["assignments"] = [
                item for item in roster["assignments"]
                if item["day"] != day or item["block"] != block
            ] + [slot]

            self.rosters[index] = roster
            save_rosters(self.rosters, self.duty_path)

        payload = self.get_duty()
        payload["ok"] = True
        payload["week"] = week
        return payload

    def clear_duty(self, week) -> dict:
        """删掉某一周排班（次数会随之回落）。"""
        try:
            week = int(week)
        except (TypeError, ValueError):
            return {"ok": False, "error": "周次不合法"}
        with self._people_lock:
            self.rosters = [r for r in self.rosters if r["week"] != week]
            save_rosters(self.rosters, self.duty_path)
        payload = self.get_duty()
        payload["ok"] = True
        return payload

    def _log_export_error(self, note: str) -> None:
        """把导出失败的原因落到 data/logs/export-error.log。

        打包后没有控制台，弹窗里只显示一行字；把完整堆栈写下来才能查。
        """
        try:
            logs = Path(sub_dir("logs"))
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(logs / "export-error.log", "a", encoding="utf-8") as handle:
                handle.write(f"\n[{stamp}] {note}\n")
        except Exception:  # noqa: BLE001
            pass

    def _ask_save_path(self, week: int) -> str:
        """让用户选保存位置；取消返回空串。

        首选 pywebview 自带的对话框：它走 Qt 主线程、和界面是同一个 GUI 栈。
        tkinter 是从 js_api 的子线程里弹的，打包后不一定可靠，只作兜底。
        """
        filename = f"第{week}周值班表.docx"

        window = self._window
        if window is not None:
            try:
                import webview

                dialog = getattr(webview, "FileDialog", None)
                dialog_type = dialog.SAVE if dialog is not None else webview.SAVE_DIALOG
                result = window.create_file_dialog(
                    dialog_type,
                    save_filename=filename,
                    file_types=("Word 文档 (*.docx)",),
                )
                if not result:
                    return ""
                if isinstance(result, (list, tuple)):
                    return str(result[0])
                return str(result)
            except Exception as exc:  # noqa: BLE001
                self._log_export_error(
                    f"pywebview 保存对话框失败，改用 tkinter：{exc!r}\n{traceback.format_exc()}"
                )

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        try:
            return filedialog.asksaveasfilename(
                title="导出值班表",
                defaultextension=".docx",
                initialfile=filename,
                initialdir=_default_save_dir(),
                filetypes=[("Word 文档", "*.docx")],
            ) or ""
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

    def export_duty(self, week, out_path=None) -> dict:
        """按模板导出某一周的值班表。

        三种情况都不该让用户"点了没下文"：
        - 不传路径：弹保存对话框，用户自选位置
        - 对话框打不开 / 选的目录不可写：退回 data/exports/，并告知原因
        - 用户取消：明确返回「已取消」
        """
        try:
            week = int(week)
        except (TypeError, ValueError):
            return {"ok": False, "error": "周次不合法"}

        with self._people_lock:
            roster = None
            for item in self.rosters:
                if item["week"] == week:
                    roster = json.loads(json.dumps(item))
                    break
            people = [p.to_dict() for p in self.people]

        if roster is None:
            return {"ok": False, "error": f"第 {week} 周还没有排班"}

        names = {}
        for person in people:
            key = duty_person_key(person)
            if key:
                names[key] = person.get("name") or key

        fallback_note = ""
        target = out_path

        if not target:
            try:
                target = self._ask_save_path(week)
            except Exception as exc:  # noqa: BLE001
                self._log_export_error(f"保存对话框打不开：{exc!r}\n{traceback.format_exc()}")
                fallback_note = "保存对话框打不开，已导出到程序目录下的 data\\exports"
                target = ""

            if not target and not fallback_note:
                return {"ok": False, "error": "已取消"}

        if not target:
            target = os.path.join(self.exports_dir, f"第{week}周值班表.docx")

        try:
            saved = export_roster(roster, names, target)
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            # 常见：目标目录没有写权限（比如存到 Program Files 或 C 盘根目录）
            if not fallback_note:
                self._log_export_error(f"写入失败：{target}\n{exc!r}\n{traceback.format_exc()}")
                try:
                    fallback = os.path.join(self.exports_dir, f"第{week}周值班表.docx")
                    saved = export_roster(roster, names, fallback)
                    return {
                        "ok": True,
                        "path": saved,
                        "note": f"原位置写不进去（{exc}），已改存到程序目录下的 data\\exports",
                    }
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": f"导出失败：{exc}"}

        return {"ok": True, "path": saved, "note": fallback_note}

    def open_exports_folder(self) -> dict:
        """打开导出目录（导出后用「打开文件夹」按钮调用）。"""
        try:
            folder = self.exports_dir
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)  # noqa: S606 - Windows 专用
            return {"ok": True, "path": folder}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

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

    def _save_people(self, allow_empty: bool = False) -> None:
        """落盘人员列表（需在 _people_lock 保护下调用）。

        allow_empty 只有「用户显式删到最后一个人」才传 True；其余自动流程
        一律走默认——避免某次读失败被当成"真的没有人"写回磁盘。
        """
        save_people(self.people, self.data_path, allow_empty=allow_empty)

    def _merge_and_save(self, person) -> bool:
        """把 person 合并进人员列表并立即落盘，返回是否为「更新已有的人」。

        读-改-写整段都在 _people_lock 内完成：批量导入跑在后台线程，
        浏览器自动入库跑在 js_api 子线程，两者交叉会导致漏更新或
        people.json 被写坏。
        """
        with self._people_lock:
            existed = any(self._same_person(p, person) for p in self.people)
            self._merge(person)
            self._save_people()
        return existed

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

    # ================= 账号管理（来自原「教务系统管理器」） =================

    def _imported_ids(self) -> set:
        """已导入课表的人员学号集合，用于判断某个账号的课表是否已入库。"""
        return {p.student_id for p in self.people if p.student_id}

    def _accounts_payload(self, result: dict = None) -> dict:
        """账号接口统一返回结构：永远带上最新账号列表与当前账号 id。"""
        payload = {
            "accounts": self.accounts.list_accounts(self._imported_ids()),
            "active_id": self.accounts.get_active_account_id(),
        }
        if result:
            payload.update(result)
        return payload

    def get_accounts(self) -> dict:
        return self._accounts_payload()

    def add_account(self, nickname, username, password, notes="") -> dict:
        return self._accounts_payload(
            self.accounts.add(nickname, username, password, notes)
        )

    def update_account(
        self, account_id, nickname="", username="", password="", notes=""
    ) -> dict:
        return self._accounts_payload(
            self.accounts.update(account_id, nickname, username, password, notes)
        )

    def delete_account(self, account_id) -> dict:
        return self._accounts_payload(self.accounts.remove(account_id))

    def set_active_account(self, account_id) -> dict:
        return self._accounts_payload(self.accounts.set_active(account_id))

    def copy_account_password(self, account_id, auto_clear_seconds=30) -> dict:
        """取出明文密码写入剪贴板（前端不展示、不持久化），30 秒后自动清空。"""
        result = self.accounts.get_password(account_id)
        if not result.get("ok"):
            return result
        seconds = int(auto_clear_seconds or 30)
        if not set_text_temporary(result["password"], seconds):
            return {"ok": False, "error": "写入剪贴板失败"}
        return {"ok": True, "cleared_in": seconds}

    def get_system_urls(self) -> dict:
        return {
            "urls": [{"label": label, "url": url} for label, url in SYSTEM_URLS],
            "current": self.accounts.get_login_url(DEFAULT_LOGIN_URL),
        }

    def set_login_url(self, url: str) -> dict:
        """保存教务系统地址。空字符串表示恢复默认。

        以前这里不做校验，粘贴错了也照存，结果点「打开并登录」只会得到一个空白页；
        现在先把地址规整一遍，不合法的直接告诉用户哪里不对。
        """
        ok, normalized, error = normalize_login_url(url)
        if not ok:
            return {
                "ok": False,
                "error": error,
                "current": self.accounts.get_login_url(DEFAULT_LOGIN_URL),
            }
        self.accounts.set_login_url(normalized)
        return {
            "ok": True,
            "current": self.accounts.get_login_url(DEFAULT_LOGIN_URL),
            "normalized": normalized,
        }

    # ============ 课表导出（内嵌浏览器 + 导出后自动入库） ============

    def _begin_browser_action(self) -> bool:
        """每个浏览器动作开始前调用。

        清除上一次的主线程异常：get_browser_status 会把 qtbridge 记录的异常当作
        browser.error 上报，如果不清，一次偶发异常会让界面**永久**显示「出错」。
        由用户的下一个动作来重置，语义上正好。
        """
        clear_last_error()
        return self._bridge is not None and self._browser is not None

    def _import_pdf_path(self, path: str, group: str = "") -> dict:
        """解析一个课表 PDF 并合并进人员列表。

        浏览器导出后自动入库、以及外部直接导入，都走这里。
        """
        try:
            person = self._load_person(path, group or "")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "path": path, "error": str(exc)}

        existed = self._merge_and_save(person)
        if person.group and not self._has_group(person.group):
            self.groups.append({"name": person.group, "parent": ""})
            save_groups(self.groups, self.groups_path)
        self.parser_version = max([p.parsed_with or 0 for p in self.people] or [0])
        return {
            "ok": True,
            "path": path,
            "name": person.name,
            "student_id": person.student_id,
            "courses": len(person.courses),
            "updated": existed,
        }

    def import_schedule_pdf(self, path: str, group: str = "") -> dict:
        """把一个课表 PDF 解析入库（不弹文件对话框，便于外部调用与测试）。"""
        result = self._import_pdf_path(path, group)
        self._notify_browser_import(result)
        payload = {"imported": result}
        payload["people"] = self.get_people()
        payload["groups"] = self.get_groups()
        return payload

    def _consume_pending_import(self) -> dict | None:
        """取走浏览器下载完的 PDF 并入库；没有待处理项时返回 None。"""
        if self._browser is None:
            return None
        path = self._browser.take_pending_import()
        if not path:
            return None
        result = self._import_pdf_path(path)
        self._notify_browser_import(result)
        return result

    def _notify_browser_import(self, result: dict) -> None:
        """把入库结果回写到浏览器窗口的提示区（必须在主线程执行）。"""
        if self._bridge is None or self._browser is None:
            return
        if result.get("ok"):
            name = result.get("name") or "未命名"
            courses = int(result.get("courses") or 0)
            self._bridge.run(
                lambda: self._browser.note_imported(name, courses)
            )
        else:
            message = result.get("error") or "解析失败"
            self._bridge.run(lambda: self._browser.note_import_failed(message))

    def get_browser_status(self) -> dict:
        """前端轮询用。顺带把已下载完的课表 PDF 解析入库。"""
        status = (
            self._browser.status()
            if self._browser is not None
            else {
                "open": False,
                "logged_in": False,
                "busy": False,
                "stage": "",
                "message": "",
                "error": "",
            }
        )
        if self._browser is not None:
            # 主线程里抛出的异常（Qt 事件循环内的）只在 qtbridge 里留了记录，
            # 由下一次用户动作清除，见 _begin_browser_action。
            error = last_error()
            if error:
                status["error"] = error.strip().splitlines()[-1]

        payload = {"browser": status}
        imported = self._consume_pending_import()
        if imported is not None:
            payload["imported"] = imported
            payload["people"] = self.get_people()
            payload["groups"] = self.get_groups()
        return payload

    def browser_login(self, account_id) -> dict:
        if not self._begin_browser_action():
            return {"ok": False, "error": "浏览器还没准备好，请稍后重试"}
        try:
            account_id = int(account_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "账号参数无效"}

        target = next(
            (a for a in self.accounts.list_accounts() if a["id"] == account_id), None
        )
        if target is None:
            return {"ok": False, "error": "账号不存在"}
        got = self.accounts.get_password(account_id)
        if not got.get("ok"):
            return {"ok": False, "error": got.get("error", "无法读取密码")}

        nickname = target["nickname"]
        username = target["username"]
        password = got["password"]
        url = self.accounts.get_login_url(DEFAULT_LOGIN_URL)
        self._bridge.run(lambda: self._browser.login(nickname, username, password, url))
        return {"ok": True, "account": nickname}

    def browser_export(self) -> dict:
        if not self._begin_browser_action():
            return {"ok": False, "error": "浏览器还没准备好，请稍后重试"}
        url = self.accounts.get_login_url(DEFAULT_LOGIN_URL)
        self._bridge.run(lambda: self._browser.export_schedule(url))
        return {"ok": True}

    def browser_open(self) -> dict:
        if not self._begin_browser_action():
            return {"ok": False, "error": "浏览器还没准备好，请稍后重试"}
        url = self.accounts.get_login_url(DEFAULT_LOGIN_URL)
        self._bridge.run(lambda: self._browser.open_page(url))
        return {"ok": True}

    def browser_hide(self) -> dict:
        """收起内嵌页面，把界面还给应用（不销毁页面，登录状态保留）。"""
        if self._bridge is not None and self._browser is not None:
            self._bridge.run(self._browser.hide)
        return {"ok": True}

    def browser_close(self) -> dict:
        if self._bridge is not None and self._browser is not None:
            self._bridge.run(self._browser.close)
        return {"ok": True}

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def maximize(self) -> None:
        if self._window is not None:
            self._window.maximize()

    def toggle_max(self) -> dict:
        """最大化 / 还原 来回切。返回切换后的状态，前端据此换图标。"""
        window = self._window
        if window is None:
            return {"ok": False, "maximized": False}

        target = not self._maximized
        try:
            if target:
                window.maximize()
            else:
                window.restore()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "maximized": self._maximized}

        self._maximized = target
        return {"ok": True, "maximized": target}

    def start_resize(self, edge) -> dict:
        """无边框窗口的边缘拖拽改大小。

        不自己算宽高：把边缘交给系统（QWindow.startSystemResize），
        拖动时的吸附、多显示器 DPI、最大化还原都由系统处理，比 JS 里
        逐帧算窗口尺寸顺滑得多，也不会抖。
        """
        edge = str(edge or "").strip()
        if edge not in _RESIZE_EDGES or self._bridge is None:
            return {"ok": False}

        def job():
            native = getattr(self._window, "native", None) if self._window else None
            if native is None:
                return
            try:
                handle = native.windowHandle()
            except Exception:  # noqa: BLE001
                handle = None
            if handle is None:
                return
            try:
                from PySide6.QtCore import Qt as QtCore

                Edge = QtCore.Edge
                names = {
                    "left": Edge.LeftEdge, "right": Edge.RightEdge,
                    "top": Edge.TopEdge, "bottom": Edge.BottomEdge,
                }
                edges = None
                for part in _RESIZE_EDGES[edge]:
                    edges = names[part] if edges is None else (edges | names[part])
                handle.startSystemResize(edges)
            except Exception:  # noqa: BLE001
                pass

        self._bridge.run(job)
        return {"ok": True}

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()
