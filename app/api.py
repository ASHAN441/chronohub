import json
import tkinter as tk
import os
import threading
from tkinter import filedialog

from .account_service import AccountService
from .clipboard import set_text_temporary
from .config import app_info
from .docx_export import export_roster, template_path
from .duty_scheduler import generate_roster, swap_candidates
from .duty_store import (
    DUTY_FILE,
    count_assignments,
    load_rosters,
    normalize_roster,
    person_key as duty_person_key,
    save_rosters,
    upsert_roster,
)
from .parser import PARSER_VERSION, parse_pdf
from .paths import base_dir
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
from .system_urls import DEFAULT_LOGIN_URL, SYSTEM_URLS


class Api:
    """暴露给前端 JS 的接口（pywebview 桥）。"""

    def __init__(
        self,
        data_path: str = None,
        groups_path: str = None,
        accounts_db_path: str = None,
        specials_path: str = None,
        duty_path: str = None,
    ):
        self._window = None
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

        self.people, self.parser_version = load_data(self.data_path)
        self.groups = load_groups(self.groups_path)
        self.specials = load_specials(self.specials_path)
        self.rosters = load_rosters(self.duty_path)
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
                self._save_people()
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

    def get_duty(self) -> dict:
        with self._people_lock:
            rosters = [json.loads(json.dumps(r)) for r in self.rosters]
            people = [p.to_dict() for p in self.people]

        names = {}
        group_of = {}
        for person in people:
            key = duty_person_key(person)
            if key:
                names[key] = person.get("name") or key
                group_of[key] = person.get("group") or ""

        return {
            "rosters": rosters,
            "counts": count_assignments(rosters),
            "names": names,
            "groups": group_of,
            "group_names": sorted({g for g in group_of.values() if g}),
            "has_template": bool(template_path()),
        }

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
        assignments, _ = generate_roster(people, specials, week, groups or {}, totals)

        roster = normalize_roster({
            "week": week,
            "groups": groups or {},
            "assignments": assignments,
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
            if item["kind"] == "dual":
                used.update(k for k in (item.get("minister"), item.get("hall")) if k)
            else:
                used.add(item.get("person") or "")

        slot = None
        for item in roster["assignments"]:
            if item["day"] == day and item["block"] == block:
                slot = item
                break
        if slot is None:
            return {"candidates": []}

        keys = swap_candidates(
            people, specials, week, roster.get("groups") or {},
            role, slot["day"], slot["start"], slot["end"], used,
        )
        names = {}
        for person in people:
            key = duty_person_key(person)
            if key:
                names[key] = person.get("name") or key
        return {"candidates": [{"key": k, "name": names.get(k, k)} for k in keys]}

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

            field = "hall" if role == "hall" else ("minister" if slot["kind"] == "dual" else "person")
            slot[field] = person_key
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

    def export_duty(self, week, out_path=None) -> dict:
        """按模板导出某一周的值班表。不传路径则弹保存对话框。"""
        try:
            week = int(week)
        except (TypeError, ValueError):
            return {"ok": False, "error": "周次不合法"}

        target = out_path
        if not target:
            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass
            try:
                target = filedialog.asksaveasfilename(
                    title="导出值班表",
                    defaultextension=".docx",
                    initialfile=f"第{week}周值班表.docx",
                    filetypes=[("Word 文档", "*.docx")],
                )
            finally:
                try:
                    root.destroy()
                except Exception:  # noqa: BLE001
                    pass
            if not target:
                return {"ok": False, "error": "已取消"}

        with self._people_lock:
            roster = None
            for item in self.rosters:
                if item["week"] == week:
                    roster = json.loads(json.dumps(item))
                    break
            people = [p.to_dict() for p in self.people]

        if roster is None:
            return {"ok": False, "error": "这一周还没有排班"}

        names = {}
        for person in people:
            key = duty_person_key(person)
            if key:
                names[key] = person.get("name") or key

        try:
            saved = export_roster(roster, names, out_path)
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"导出失败：{exc}"}

        return {"ok": True, "path": saved}

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

    def _save_people(self) -> None:
        """落盘人员列表（需在 _people_lock 保护下调用）。"""
        save_people(self.people, self.data_path)

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
        self.accounts.set_login_url(url or "")
        return {
            "ok": True,
            "current": self.accounts.get_login_url(DEFAULT_LOGIN_URL),
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

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()
