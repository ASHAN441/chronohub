import re

import pdfplumber

from .models import Course, Person


_PERIOD_RE = re.compile(r"\((\d+)\s*-\s*(\d+)\s*节\)")
_FIELD_KEYS = (
    "校区",
    "场地",
    "地点",
    "教师",
    "教学班组成",
    "教学班",
    "课程性质简称",
    "课程性质",
    "考核方式",
    "选课备注",
    "课程学时组成",
    "周学时",
    "总学时",
    "学分",
    # 「重修标记:」是每门课信息块的**最后一个**字段。同一格里有第二门课时，
    # 它会长在第二门课名字前面（PDF 里两段文字是连着的），必须当字段剥掉，
    # 否则课名会变成「重修标记:人工智能专业外语」。
    "重修标记",
)
_FIELD_KEY_RE = re.compile("^(?:" + "|".join(_FIELD_KEYS) + ")")
_WEEKDAY_MAP = {
    "星期一": 1,
    "星期二": 2,
    "星期三": 3,
    "星期四": 4,
    "星期五": 5,
    "星期六": 6,
    "星期日": 7,
}
_DEFAULT_WEEKS = list(range(1, 19))

# 识别算法版本：每次修改解析逻辑都要 +1，软件会据此提示/自动刷新已导入的课表。
# v3：修「重修标记:」被粘进下一门课名字的问题（同一格多门课时会出现）。
PARSER_VERSION = 3


def parse_weeks(text: str) -> list:
    """把 '1-14周'、'2-18周(双)'、'第2周'、'6-7周,9-10周' 展开成周次列表。"""
    weeks = set()
    for raw in str(text).replace("，", ",").split(","):
        part = raw.strip()
        if not part:
            continue

        part = part.replace("第", "")
        parity = None
        if "(单)" in part:
            parity = "odd"
            part = part.replace("(单)", "")
        elif "(双)" in part:
            parity = "even"
            part = part.replace("(双)", "")
        part = part.replace("周", "")
        if not part:
            continue

        if "-" in part:
            pieces = part.split("-")
            if len(pieces) != 2:
                continue
            lo_text, hi_text = pieces
        else:
            lo_text = hi_text = part

        if not (lo_text.isdigit() and hi_text.isdigit()):
            continue

        lo, hi = int(lo_text), int(hi_text)
        for week in range(lo, hi + 1):
            if parity == "odd" and week % 2 == 0:
                continue
            if parity == "even" and week % 2 != 0:
                continue
            weeks.add(week)

    return sorted(weeks)


def _field(details: str, key: str) -> str:
    match = re.search(r"/" + key + r":([^/]*)", details)
    return match.group(1) if match else ""


def _location(details: str) -> str:
    """场地字段：网格版式叫「场地」，列表版式叫「地点」。"""
    return _field(details, "场地") or _field(details, "地点")


def _is_course_name(name: str) -> bool:
    """过滤掉被截断后只剩字段值的假课程名。"""
    if not name:
        return False
    if _FIELD_KEY_RE.match(name):
        return False
    if re.fullmatch(r"[\d.、，,;；:：/()（）\s]*", name):
        return False
    return True


def _clean_name(raw: str) -> str:
    """去掉混入课程名的上一门课尾部内容（常见于同一单元格有多门课）。"""
    name = raw or ""
    credits = list(re.finditer(r"学分:[\d.]+", name))
    if credits:
        name = name[credits[-1].end():]
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    # 字段残留形如 "课程学时组成:讲课学时:30课程名"，取最后一个冒号后的内容。
    # 注意不能对所有冒号都切分，课名本身可能含冒号（如「法眼观万象：生活化法律案例探究」）。
    if re.search("(?:" + "|".join(_FIELD_KEYS) + r")[:：]", name):
        name = re.split(r"[:：]", name)[-1]
    name = re.sub(r"^[\d.、，,;；]+", "", name.strip())
    # 双保险：万一上面没切干净（比如字段名后紧跟中文），这里直接剥掉前缀。
    # 「重修标记」属于上一门课，不该出现在这门课的名字里。
    name = re.sub(r"^(?:重修标记|重修)[:：]?", "", name.strip())
    return name.strip()


def _parse_cell(text, day: int) -> list:
    """解析网格课表的一个单元格（星期 × 节次），可能包含多门课。"""
    flat = re.sub(r"\s+", "", text or "")
    markers = list(_PERIOD_RE.finditer(flat))
    courses = []
    for index, marker in enumerate(markers):
        begin = markers[index - 1].end() if index else 0
        finish = markers[index + 1].start() if index + 1 < len(markers) else len(flat)
        start = int(marker.group(1))
        end = int(marker.group(2))
        if not (1 <= start <= end <= 14):
            continue
        name = _clean_name(flat[begin:marker.start()])
        if not name or not _is_course_name(name):
            continue
        details = flat[marker.end():finish]
        courses.append(
            Course(
                name=name,
                day=day,
                start=start,
                end=end,
                weeks=parse_weeks(details.split("/", 1)[0]) or list(_DEFAULT_WEEKS),
                location=_location(details),
                teacher=_field(details, "教师"),
            )
        )
    return courses


def _parse_meta(text) -> tuple:
    """解析表头信息：姓名、学号、学期。"""
    text = re.sub(r"\s+", "", text or "")
    name = ""
    student_id = ""
    semester = ""

    match = re.search(r"^(.*?)课表", text)
    if match:
        name = match.group(1)
    match = re.search(r"(\d{4}-\d{4}学年第\d+学期)", text)
    if match:
        semester = match.group(1)
    match = re.search(r"学号[:：](\d+)", text)
    if match:
        student_id = match.group(1)
    return name, student_id, semester


def _apply_meta(person: Person, name: str, student_id: str, semester: str) -> None:
    if name:
        person.name = name
    if student_id:
        person.student_id = student_id
    if semester:
        person.semester = semester


def _find_header_row(table) -> int:
    for index, row in enumerate(table):
        if len(row) >= 3 and (row[1] or "").strip() == "节次":
            return index
    return -1


def _parse_period_range(text) -> tuple:
    """把 '1-2'、'7-8'、'5' 解析成 (起始节次, 结束节次)。"""
    text = re.sub(r"\s+", "", text or "")
    if not text:
        return None
    if "-" in text:
        parts = text.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
        return None
    if text.isdigit():
        return int(text), int(text)
    return None


def _parse_list_course(text):
    """解析列表格式的课程文本，返回 Course（星期与节次由调用方补齐）。"""
    flat = re.sub(r"\s+", "", text or "")
    if not flat:
        return None

    # 版式三：周数: 1-18周/校区: 主校区/地点: 博文212/教师: 孙宇婷/教学班:广告学概论-0001/...
    if "周数:" in flat:
        week_match = re.search(r"周数:([^/]*)", flat)
        weeks = parse_weeks(week_match.group(1)) if week_match else []
        class_text = _field(flat, "教学班")
        name = re.sub(r"-\d+.*$", "", class_text).strip()
        if not name:
            return None
        return Course(
            name=name,
            day=0,
            start=0,
            end=0,
            weeks=weeks or list(_DEFAULT_WEEKS),
            location=_location(flat),
            teacher=_field(flat, "教师"),
        )

    # 版式二：课程名 教师: 姓名
    spaced = re.sub(r"\s+", " ", text or "").strip()
    match = re.search(r"\s*教师\s*[:：]\s*(.*)$", spaced)
    if match:
        name = spaced[: match.start()].strip()
        teacher = match.group(1).strip()
    else:
        name = spaced
        teacher = ""
    if not name:
        return None
    return Course(
        name=name,
        day=0,
        start=0,
        end=0,
        weeks=list(_DEFAULT_WEEKS),
        teacher=teacher,
    )


def _weekday_columns(table, header_index: int) -> dict:
    """从表头读出「列号 → 星期」。"""
    mapping = {}
    for column, cell in enumerate(table[header_index]):
        key = re.sub(r"\s+", "", cell or "")
        if key in _WEEKDAY_MAP:
            mapping[column] = _WEEKDAY_MAP[key]
    return mapping


def _row_label(row) -> str:
    """节次列的文字，例如 '1'、'7'。续行（跨页被切断的行）为空。"""
    if len(row) < 2:
        return ""
    return re.sub(r"\s+", "", row[1] or "")


def _has_period(text: str) -> bool:
    return bool(_PERIOD_RE.search(text or ""))


def _collect_grid_cells(pages):
    """把网格课表的单元格文本按星期收集起来，并把跨页被切断的内容接回上一格。"""
    person = Person(name="未命名")
    cells = {}
    mapping = {}
    for page in pages:
        for table in page.extract_tables() or []:
            if not table:
                continue

            header_index = _find_header_row(table)
            if header_index >= 0:
                mapping = _weekday_columns(table, header_index) or mapping
                if header_index >= 1:
                    name, student_id, semester = _parse_meta(table[header_index - 1][0])
                    _apply_meta(person, name, student_id, semester)
                data_rows = table[header_index + 1:]
            else:
                data_rows = table

            if not mapping:
                continue

            for row in data_rows:
                # 换页可能把同一格切成两半：一行没有节次号，或者上一格还没有
                # 出现节次标记，都说明这一格是上一格的续写。
                continuation = not _row_label(row)
                for column, day in mapping.items():
                    if column >= len(row):
                        continue
                    text = re.sub(r"\s+", "", row[column] or "")
                    if not text:
                        continue
                    bucket = cells.setdefault(day, [])
                    if bucket and (continuation or not _has_period(bucket[-1])):
                        # 上一页同一列最后一格被页码切断，这里补上剩余内容。
                        bucket[-1] += text
                    else:
                        bucket.append(text)
    return person, cells


def _dedupe(courses) -> list:
    """同一门课重复出现时只保留一条，避免热力图重复计算。"""
    seen = set()
    unique = []
    for course in courses:
        key = (
            course.name,
            course.day,
            course.start,
            course.end,
            tuple(course.weeks),
            course.location,
            course.teacher,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(course)
    return unique


def _parse_grid(pages) -> Person:
    person, cells = _collect_grid_cells(pages)
    courses = []
    for day in sorted(cells):
        for text in cells[day]:
            courses.extend(_parse_cell(text, day))
    person.courses = _dedupe(sorted(courses, key=lambda c: (c.day, c.start, c.end)))
    return person


def _parse_list(pages) -> Person:
    person = Person(name="未命名")
    for page in pages:
        for table in page.extract_tables() or []:
            if not table:
                continue
            current_day = None
            last_period = None
            for row in table:
                col0 = (row[0] or "").strip() if len(row) > 0 else ""
                col1 = (row[1] or "").strip() if len(row) > 1 else ""
                col2 = (row[2] or "").strip() if len(row) > 2 else ""

                if "课表" in col0 and "学号" in col0:
                    name, student_id, semester = _parse_meta(col0)
                    _apply_meta(person, name, student_id, semester)
                    continue
                if col0 in _WEEKDAY_MAP:
                    current_day = _WEEKDAY_MAP[col0]

                period = _parse_period_range(col1)
                if period:
                    last_period = period

                if current_day is None or not col2:
                    continue
                use_period = period if period else last_period
                if not use_period:
                    continue

                course = _parse_list_course(col2)
                if course is None:
                    continue
                course.day = current_day
                course.start = use_period[0]
                course.end = use_period[1]
                person.courses.append(course)
    person.courses = _dedupe(person.courses)
    return person


def _is_list_format(table) -> bool:
    if not table:
        return False
    for row in table:
        if len(row) >= 3 and (row[1] or "").strip() == "节次":
            return False
    for row in table[1:]:
        if len(row) >= 1 and (row[0] or "").strip() in _WEEKDAY_MAP:
            return True
    return False


def _first_table(pages) -> list:
    for page in pages:
        for table in page.extract_tables() or []:
            if table:
                return table
    return None


def parse_pdf(path: str) -> Person:
    """解析教务系统导出的课表 PDF，返回一个人的结构化课表。"""
    with pdfplumber.open(path) as pdf:
        pages = list(pdf.pages)
        if not pages:
            return Person(name="未命名")
        first_table = _first_table(pages)
        if _is_list_format(first_table):
            return _parse_list(pages)
        return _parse_grid(pages)
