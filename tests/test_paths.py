"""路径解析的单元测试（打包正确性的回归保护）。

为什么值得单独测：PyInstaller onefile 打包后 `__file__` 指向临时解压目录
（`sys._MEIPASS`），重启即消失。如果可写数据被算到那里，用户下次打开会发现
**导入的课表全没了**——这种 bug 在开发环境下永远复现不出来，只能靠测试钉住。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import paths  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 注意要用 abspath 规整：os.path.join(os.sep, "some", ...) 在 Windows 上得到的是
# 驱动器相对路径（\some\dir），而 paths 内部会做 abspath，两边对不上。
FAKE_DIR = os.path.abspath(os.path.join(os.sep, "some", "dir"))
FAKE_EXE = os.path.join(FAKE_DIR, "ChronoHub.exe")
FAKE_MEIPASS = os.path.abspath(os.path.join(os.sep, "tmp", "_MEI123456"))


class DevModeTest(unittest.TestCase):
    """未打包时：项目根目录既放数据也放资源。"""

    def test_not_frozen(self):
        self.assertFalse(paths.is_frozen())

    def test_root_is_project_root(self):
        self.assertEqual(paths.base_dir(), ROOT)
        self.assertEqual(paths.resource_dir(), ROOT)

    def test_data_dir_under_project(self):
        self.assertEqual(paths.data_dir(create=False), os.path.join(ROOT, "data"))


class FrozenModeTest(unittest.TestCase):
    """打包后：数据在 exe 同级，只读资源在 _MEIPASS。两者必须分开。"""

    def setUp(self):
        self._patches = [
            mock.patch.object(paths.sys, "frozen", True, create=True),
            mock.patch.object(paths.sys, "executable", FAKE_EXE),
            mock.patch.object(paths.sys, "_MEIPASS", FAKE_MEIPASS, create=True),
        ]
        for patch in self._patches:
            patch.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def test_is_frozen(self):
        self.assertTrue(paths.is_frozen())

    def test_data_goes_next_to_exe_not_into_meipass(self):
        self.assertEqual(paths.base_dir(), os.path.dirname(FAKE_EXE))
        self.assertEqual(
            paths.data_dir(create=False),
            os.path.join(os.path.dirname(FAKE_EXE), "data"),
        )
        self.assertFalse(
            paths.data_dir(create=False).startswith(FAKE_MEIPASS),
            "数据目录绝不能落在 _MEIPASS（临时解压目录）里",
        )

    def test_resources_come_from_meipass(self):
        self.assertEqual(paths.resource_dir(), FAKE_MEIPASS)

    def test_no_meipass_falls_back_to_exe_dir(self):
        """_MEIPASS 缺失或为空时不能崩，要退回到 exe 同级目录。"""
        self._patches[2].stop()  # 撤掉 setUp 里对 _MEIPASS 的 patch
        with mock.patch.object(paths.sys, "_MEIPASS", None, create=True):
            self.assertEqual(paths.resource_dir(), FAKE_DIR)


class ResourceLayoutTest(unittest.TestCase):
    """界面文件必须能被找到，否则打包后是个白窗口。"""

    def test_web_index_exists(self):
        index = os.path.join(paths.resource_dir(), "web", "index.html")
        self.assertTrue(os.path.exists(index), f"缺少界面文件：{index}")

    def test_icon_exists_for_build(self):
        icon = os.path.join(ROOT, "packaging", "ChronoHub.ico")
        self.assertTrue(os.path.exists(icon), f"缺少打包图标：{icon}")

    def test_spec_exists_and_points_at_web(self):
        spec = os.path.join(ROOT, "packaging", "ChronoHub.spec")
        self.assertTrue(os.path.exists(spec))
        with open(spec, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn('"web"', content, "spec 必须把 web/ 作为数据一起打包")
        self.assertIn("console=False", content, "窗口程序不应留控制台")

    def test_spec_has_boot_splash(self):
        """打包启动图要打进 exe —— 它负责盖住「解压」那段黑屏。"""
        spec = os.path.join(ROOT, "packaging", "ChronoHub.spec")
        with open(spec, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("Splash(", content, "spec 必须声明启动图")
        self.assertIn("splash.png", content)
        self.assertIn("splash=splash", content, "启动图要传给 EXE，否则不生效")
        self.assertTrue(
            os.path.exists(os.path.join(ROOT, "packaging", "splash.png")),
            "仓库里应有 packaging/splash.png",
        )

    def test_spec_bundles_duty_template(self):
        """值班表导出模板必须打进包里，否则打包后一点「导出」就报找不到模板。"""
        spec = os.path.join(ROOT, "packaging", "ChronoHub.spec")
        with open(spec, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("值班表.docx", content, "spec 必须把值班表模板一起打包")
        self.assertTrue(
            os.path.exists(os.path.join(ROOT, "值班表.docx")),
            "仓库根目录应有值班表.docx 模板",
        )


class DataPathConsumersTest(unittest.TestCase):
    """所有**可写**路径都必须派生自 paths.data_dir()。

    这是打包后实测踩到的：exe 同级只生成了 data/webview，没有 accounts.db——
    因为 account_service 还在用 __file__ 推算路径，账号库被建到了临时解压目录，
    **重启就丢账号**。这里把每一个可写路径都钉住。
    """

    def setUp(self):
        self.data_dir = os.path.normcase(paths.data_dir(create=False))

    def test_account_db_is_under_data_dir(self):
        from app.account_service import default_db_path

        self.assertEqual(
            os.path.normcase(os.path.dirname(default_db_path())),
            self.data_dir,
            "账号库必须放在 data/ 下（打包后即 exe 同级）",
        )

    def test_people_and_groups_files_are_under_data_dir(self):
        from app.people_store import DATA_FILE, GROUPS_FILE

        for path in (DATA_FILE, GROUPS_FILE):
            self.assertEqual(
                os.path.normcase(os.path.dirname(path)),
                self.data_dir,
                f"{os.path.basename(path)} 必须放在 data/ 下",
            )

    def test_pdf_staging_dir_is_under_data_dir(self):
        from app.browser import staging_dir

        self.assertEqual(
            os.path.normcase(os.path.dirname(staging_dir())),
            self.data_dir,
            "导出的 PDF 暂存目录必须放在 data/ 下",
        )

    def test_browser_profile_dir_is_under_data_dir(self):
        from app.browser import profile_dir

        self.assertEqual(
            os.path.normcase(os.path.dirname(profile_dir())),
            self.data_dir,
            "内嵌浏览器的 profile 必须放在 data/ 下",
        )

    def test_no_module_recomputes_project_root_from_file(self):
        """除了 paths.py，任何模块都不该再用 __file__ 推算项目根。"""
        app_dir = os.path.join(ROOT, "app")
        offenders = []
        for name in sorted(os.listdir(app_dir)):
            if not name.endswith(".py") or name == "paths.py":
                continue
            with open(os.path.join(app_dir, name), encoding="utf-8") as handle:
                content = handle.read()
            if "abspath(__file__)" in content:
                offenders.append(name)
        self.assertEqual(
            offenders,
            [],
            f"这些模块还在用 __file__ 推算路径，打包后会写进临时目录：{offenders}",
        )


if __name__ == "__main__":
    unittest.main()
