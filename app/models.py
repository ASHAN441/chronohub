from dataclasses import asdict, dataclass, field
from typing import List


@dataclass
class Course:
    """一门课在某一星期、某段节次、某些周次的上课安排。"""

    name: str
    day: int
    start: int
    end: int
    weeks: List[int]
    location: str = ""
    teacher: str = ""


@dataclass
class Person:
    """一个人的课表。day 取值 1-7，对应周一至周日。"""

    name: str
    student_id: str = ""
    semester: str = ""
    group: str = ""
    source: str = ""
    parsed_with: int = 0
    courses: List[Course] = field(default_factory=list)

    def is_busy(self, day: int, period: int, week: int) -> bool:
        return any(
            course.day == day
            and course.start <= period <= course.end
            and week in course.weeks
            for course in self.courses
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Person":
        return cls(
            name=data.get("name", ""),
            student_id=data.get("student_id", ""),
            semester=data.get("semester", ""),
            group=data.get("group", ""),
            source=data.get("source", ""),
            parsed_with=int(data.get("parsed_with") or 0),
            courses=[Course(**course) for course in data.get("courses", [])],
        )
