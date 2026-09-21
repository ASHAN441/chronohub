import os
import unittest
from collections import Counter

import pdfplumber

from app.parser import (
    _PERIOD_RE,
    _clean_name,
    _collect_grid_cells,
    _parse_cell,
    parse_pdf,
    parse_weeks,
)


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PDF = os.path.join(FIXTURE_DIR, "sample_grid.pdf")
LIST_PDF = os.path.join(FIXTURE_DIR, "sample_list.pdf")
SPAN_PDF = os.path.join(FIXTURE_DIR, "sample_span.pdf")
MULTI_PAGE_PDF = os.path.join(FIXTURE_DIR, "sample_multi_page.pdf")


class FakePage:
    """只提供 extract_tables 的假页面，用来直接测试单元格拼接逻辑。"""

    def __init__(self, table):
        self.table = table

    def extract_tables(self):
        return [self.table]


def weekday_row(*cells):
    """构造一行：时间段、节次、周一 ~ 周日。"""
    row = ["", "", "", "", "", "", "", "", ""]
    for index, cell in enumerate(cells):
        row[index] = cell
    return row


class ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.person = parse_pdf(SAMPLE_PDF)

    def test_metadata(self):
        self.assertEqual(self.person.name, "伍雅芳")
        self.assertEqual(self.person.student_id, "25183032")
        self.assertIn("2026-2027", self.person.semester)

    def test_total_course_count(self):
        self.assertEqual(len(self.person.courses), 20)

    def test_even_week_course(self):
        matches = [c for c in self.person.courses if c.name == "线性代数" and c.start == 3]
        self.assertEqual(len(matches), 1)
        course = matches[0]
        self.assertEqual(course.weeks, list(range(2, 19, 2)))
        self.assertEqual(course.location, "博文108")
        self.assertEqual(course.teacher, "于智旺")

    def test_odd_week_course(self):
        matches = [c for c in self.person.courses if c.name == "酒店运营管理" and c.start == 3]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].weeks, list(range(1, 18, 2)))

    def test_multi_period_course(self):
        matches = [c for c in self.person.courses if c.name == "形势与政策2"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].start, 5)
        self.assertEqual(matches[0].end, 8)
        self.assertEqual(matches[0].weeks, [13, 14])

    def test_no_course_on_sunday(self):
        sunday_courses = [c for c in self.person.courses if c.day == 7]
        self.assertEqual(sunday_courses, [])

    def test_parse_weeks_with_di_prefix(self):
        self.assertEqual(parse_weeks("第2周"), [2])
        self.assertEqual(parse_weeks("第12周"), [12])
        self.assertEqual(parse_weeks("第1-14周"), list(range(1, 15)))

    def test_parse_weeks_multiple_ranges(self):
        self.assertEqual(parse_weeks("6-7周,9-10周"), [6, 7, 9, 10])
        self.assertEqual(parse_weeks("3-11周,13-14周"), list(range(3, 12)) + [13, 14])
        self.assertEqual(parse_weeks("1-5周(单),6-18周"), [1, 3, 5] + list(range(6, 19)))
        self.assertEqual(parse_weeks("1-3周(单),4-8周"), [1, 3] + list(range(4, 9)))
        self.assertEqual(parse_weeks("1-5周，7-9周"), [1, 2, 3, 4, 5, 7, 8, 9])

    def test_parse_weeks_ignores_invalid_parts(self):
        self.assertEqual(parse_weeks("??"), [])
        self.assertEqual(parse_weeks("1-2-3周"), [])

    def test_clean_name_strips_previous_block_tail(self):
        self.assertEqual(
            _clean_name("25-8班;计科合作办学25-9班/总学时:54/学分:2.0线性代数"),
            "线性代数",
        )
        self.assertEqual(_clean_name("线性代数"), "线性代数")
        self.assertEqual(_clean_name("毛泽东思想和中国特色社会主义理论体系概论"), "毛泽东思想和中国特色社会主义理论体系概论")

    def test_list_format_pdf(self):
        person = parse_pdf(LIST_PDF)
        self.assertEqual(person.name, "闫爱琳")
        self.assertEqual(person.student_id, "25018005")
        self.assertEqual(len(person.courses), 18)

        matches = [c for c in person.courses if c.name == "广告学概论" and c.day == 1]
        self.assertEqual(len(matches), 1)
        course = matches[0]
        self.assertEqual(course.day, 1)
        self.assertEqual(course.start, 1)
        self.assertEqual(course.end, 2)
        self.assertEqual(course.weeks, list(range(1, 19)))
        self.assertEqual(course.location, "博文212")
        self.assertEqual(course.teacher, "孙宇婷")

    def test_list_format_parses_name_from_class_field(self):
        person = parse_pdf(LIST_PDF)
        names = {c.name for c in person.courses}
        self.assertIn("体育3", names)
        self.assertIn("中国新闻事业史", names)
        self.assertNotIn("周数: 1-18周/校区: 主校区/地点: 博文212/", names)

    def test_list_format_multi_range_weeks(self):
        person = parse_pdf(LIST_PDF)
        matches = [c for c in person.courses if c.name == "新闻编辑学" and c.day == 1]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].weeks, [1, 2] + list(range(4, 19)))


class CellParsingTest(unittest.TestCase):
    """单元格切分：同一格多门课、缺字段、课名带冒号等情况。"""

    def test_course_without_credit_field_is_kept(self):
        """被页码切断、只剩半截字段的课程也要解析出来。"""
        courses = _parse_cell(
            "线性代数(5-6节)1-18周/校区:主校区/场地:博文108/教师:于智旺",
            day=1,
        )
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].name, "线性代数")
        self.assertEqual(courses[0].start, 5)
        self.assertEqual(courses[0].end, 6)
        self.assertEqual(courses[0].weeks, list(range(1, 19)))
        self.assertEqual(courses[0].location, "博文108")
        self.assertEqual(courses[0].teacher, "于智旺")

    def test_multiple_courses_in_one_cell(self):
        text = (
            "数字化设计表现2(1-4节)1-5周/校区:主校区/场地:美术332/教师:王子源/学分:2.5"
            "视觉语言设计基础(1-4节)6-7周,9-10周/校区:主校区/场地:美术332/教师:刘佳/学分:3.0"
        )
        courses = _parse_cell(text, day=1)
        self.assertEqual([c.name for c in courses], ["数字化设计表现2", "视觉语言设计基础"])
        self.assertEqual(courses[1].weeks, [6, 7, 9, 10])
        self.assertEqual(courses[1].teacher, "刘佳")

    def test_course_name_keeps_colon(self):
        courses = _parse_cell(
            "法眼观万象：生活化法律案例探究(9-10节)1-18周/校区:主校区/场地:国商201/教师:方芳/学分:2.0",
            day=3,
        )
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].name, "法眼观万象：生活化法律案例探究")

    def test_field_leftover_is_not_treated_as_course(self):
        """只有上一门课的字段尾巴、没有课名时不能凭空造出一门课。"""
        courses = _parse_cell(
            "(5-6节)1-18周/校区:主校区/场地:国商210/教师:樊晓倩/学分:3.0",
            day=1,
        )
        self.assertEqual(courses, [])

    def test_name_split_across_pages_is_joined(self):
        courses = _parse_cell(
            "毛泽东思想和中国特色(1-2节)1-15周/校区:主校区/场地:博文107/教师:张咏涛/学分:3.0",
            day=1,
        )
        self.assertEqual(courses[0].name, "毛泽东思想和中国特色")


class CrossPageCellTest(unittest.TestCase):
    """换页把同一个格子切成两半时的拼接。"""

    HEADER = [
        ["张三课表2026-2027学年第1学期 学号：20260001"],
        ["时间段", "节次", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
        weekday_row("上午", "1", "线性代数"),
        weekday_row("", "2"),
    ]

    def test_continuation_row_without_period_label(self):
        page2 = [
            weekday_row(
                "",
                "",
                "(1-2节)1-18周/校区:主校区/场地:博文108/教师:于智旺/学分:3.0",
            )
        ]
        _, cells = _collect_grid_cells([FakePage(self.HEADER), FakePage(page2)])
        courses = []
        for text in cells[1]:
            courses.extend(_parse_cell(text, day=1))
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].name, "线性代数")
        self.assertEqual(courses[0].weeks, list(range(1, 19)))

    def test_previous_cell_without_period_marker_is_continued(self):
        """上一格只有课名（节次与字段被切到下一页）时也要接上。"""
        page1 = [
            self.HEADER[0],
            self.HEADER[1],
            weekday_row(
                "上午",
                "1",
                "线性代数(1-2节)1-18周/校区:主校区/场地:博文108/教师:于智旺/学分:3.0",
            ),
            weekday_row("", "5", "货币金融学"),
        ]
        page2 = [
            weekday_row(
                "",
                "6",
                "(5-6节)1-18周/校区:主校区/场地:国商210/教师:樊晓倩/学分:3.0",
            )
        ]
        _, cells = _collect_grid_cells([FakePage(page1), FakePage(page2)])
        courses = []
        for text in cells[1]:
            courses.extend(_parse_cell(text, day=1))
        self.assertEqual([c.name for c in courses], ["线性代数", "货币金融学"])
        self.assertEqual(courses[1].start, 5)
        self.assertEqual(courses[1].end, 6)
        self.assertEqual(courses[1].location, "国商210")
        self.assertEqual(courses[1].weeks, list(range(1, 19)))

    def test_distinct_rows_are_not_joined(self):
        page1 = [
            self.HEADER[0],
            self.HEADER[1],
            weekday_row("上午", "1", "线性代数(1-2节)1-18周/校区:主校区/场地:博文108/教师:于智旺"),
            weekday_row("", "3", "高等数学(3-4节)1-18周/校区:主校区/场地:博文109/教师:李四"),
        ]
        _, cells = _collect_grid_cells([FakePage(page1)])
        courses = []
        for text in cells[1]:
            courses.extend(_parse_cell(text, day=1))
        self.assertEqual([c.name for c in courses], ["线性代数", "高等数学"])


class CrossPageFixtureTest(unittest.TestCase):
    """真实课表（跨页切断）的回归测试。"""

    @classmethod
    def setUpClass(cls):
        cls.person = parse_pdf(SPAN_PDF)

    def test_metadata(self):
        self.assertEqual(self.person.name, "于立霖")
        self.assertEqual(self.person.student_id, "25201036")

    def test_total_course_count(self):
        self.assertEqual(len(self.person.courses), 21)

    def test_course_split_by_page_break_is_recovered(self):
        matches = [c for c in self.person.courses if c.name == "货币金融学" and c.day == 1]
        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0].start, matches[0].end), (5, 6))
        self.assertEqual(matches[0].location, "国商210")
        self.assertEqual(matches[0].teacher, "樊晓倩")

    def test_course_name_cut_by_page_break_is_complete(self):
        names = {c.name for c in self.person.courses}
        self.assertIn("毛泽东思想和中国特色社会主义理论体系概论", names)
        self.assertNotIn("社会主义理论体系概论", names)

    def test_saturday_courses(self):
        saturday = Counter(c.name for c in self.person.courses if c.day == 6)
        self.assertEqual(
            saturday,
            Counter(
                {
                    "思想政治理论实践课": 2,
                    "大学生健康教育": 1,
                    "形势与政策2": 1,
                }
            ),
        )


class MultiPageFixtureTest(unittest.TestCase):
    """三页课表的回归测试。"""

    @classmethod
    def setUpClass(cls):
        cls.person = parse_pdf(MULTI_PAGE_PDF)

    def test_total_course_count(self):
        self.assertEqual(len(self.person.courses), 23)

    def test_night_period_course(self):
        matches = [c for c in self.person.courses if c.name == "生物化学实验"]
        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0].start, matches[0].end), (9, 11))
        self.assertEqual(matches[0].weeks, list(range(2, 14)))

    def test_same_course_split_between_teachers(self):
        matches = [c for c in self.person.courses if c.name == "生物化学" and c.day == 1]
        self.assertEqual([c.teacher for c in matches], ["逄洪波", "张奇"])
        self.assertEqual(matches[0].weeks, list(range(1, 9)))
        self.assertEqual(matches[1].weeks, list(range(9, 17)))


class MarkerCoverageTest(unittest.TestCase):
    """每一处 (N-M节) 标记都应该对应一门解析出的课程。"""

    def assert_markers_match_courses(self, path):
        with pdfplumber.open(path) as pdf:
            _, cells = _collect_grid_cells(list(pdf.pages))
        markers = sum(
            len(_PERIOD_RE.findall(text)) for texts in cells.values() for text in texts
        )
        person = parse_pdf(path)
        self.assertEqual(markers, len(person.courses), path)

    def test_grid_fixture(self):
        self.assert_markers_match_courses(SAMPLE_PDF)

    def test_span_fixture(self):
        self.assert_markers_match_courses(SPAN_PDF)

    def test_multi_page_fixture(self):
        self.assert_markers_match_courses(MULTI_PAGE_PDF)


if __name__ == "__main__":
    unittest.main()
