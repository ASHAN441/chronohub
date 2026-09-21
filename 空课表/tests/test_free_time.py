import unittest

from app.free_time import free_people_all_weeks, free_people_at
from app.models import Course, Person


def make_person(name, blocks):
    courses = [
        Course(name=name, day=day, start=start, end=end, weeks=weeks)
        for day, start, end, weeks in blocks
    ]
    return Person(name=name, courses=courses)


class FreeTimeTest(unittest.TestCase):
    def setUp(self):
        self.a = make_person("A", [(1, 1, 2, [1, 2, 3])])
        self.b = make_person("B", [(1, 1, 2, [1])])
        self.c = make_person("C", [])

    def test_free_people_at(self):
        people = [self.a, self.b, self.c]
        self.assertEqual(free_people_at(people, 1, 1, 1), [self.c])
        self.assertEqual(free_people_at(people, 1, 1, 2), [self.b, self.c])
        self.assertEqual(free_people_at(people, 1, 3, 1), [self.a, self.b, self.c])

    def test_free_people_all_weeks(self):
        self.assertEqual(free_people_all_weeks([self.a, self.b, self.c], 1, 1), [self.c])


if __name__ == "__main__":
    unittest.main()

