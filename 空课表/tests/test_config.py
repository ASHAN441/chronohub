import unittest

from app.config import APP_NAME, DEVELOPER, VERSION, app_info


class ConfigTest(unittest.TestCase):
    def test_app_info(self):
        info = app_info()
        self.assertEqual(info["name"], APP_NAME)
        self.assertEqual(info["version"], VERSION)
        self.assertEqual(info["developer"], DEVELOPER)
        self.assertEqual(info["version"], "2.1")
        self.assertIn("copyright", info)
        self.assertIn(DEVELOPER, info["copyright"])


if __name__ == "__main__":
    unittest.main()




