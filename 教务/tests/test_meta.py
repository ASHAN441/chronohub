"""应用元信息的单元测试。"""

from __future__ import annotations

import unittest

from app import meta


class MetaTest(unittest.TestCase):
    def test_version_is_semver(self) -> None:
        parts = meta.APP_VERSION.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_identity_present(self) -> None:
        self.assertTrue(meta.APP_NAME)
        self.assertTrue(meta.DEVELOPER)
        self.assertIn(meta.DEVELOPER, meta.COPYRIGHT)

    def test_email_format(self) -> None:
        self.assertIn("@", meta.EMAIL)
        self.assertIn(".", meta.EMAIL.split("@")[1])


if __name__ == "__main__":
    unittest.main()
