DEFAULT_MAX_WEEK = 18


def free_people_at(people, day: int, period: int, week: int) -> list:
    """返回在指定星期、节次、周次没有课的人。"""
    return [person for person in people if not person.is_busy(day, period, week)]


def free_people_all_weeks(people, day: int, period: int, max_week: int = DEFAULT_MAX_WEEK) -> list:
    """返回在所有周次（1..max_week）都空闲的人。"""
    return [
        person
        for person in people
        if all(not person.is_busy(day, period, week) for week in range(1, max_week + 1))
    ]

