"""Api 类对外/对内方法的"形状"检查。

为什么单独建一个文件：
Python 的类体是顺序执行的，**后定义的同名方法会静默覆盖前面的**，
不报错、不警告，只有真正调用时才炸。我们踩过这个坑 —— 数据包用的
`_ask_save_path` 把值班表用的 `_ask_save_path` 顶掉了，于是导出值班表时
保存对话框永远打不开（缺参数 TypeError 被上层 try 吞掉，用户只看到
一句"保存对话框打不开"）。这里把它钉死。
"""

import ast
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.api import Api  # noqa: E402


class NoShadowedMethodTest(unittest.TestCase):
    def test_api_class_has_no_duplicate_method_names(self):
        path = os.path.join(ROOT, "app", "api.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        api_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Api"
        )

        seen = {}
        duplicates = []
        for node in api_class.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in seen:
                duplicates.append(
                    f"{node.name}（第 {seen[node.name]} 行 与 第 {node.lineno} 行）"
                )
            else:
                seen[node.name] = node.lineno

        self.assertEqual(
            duplicates,
            [],
            "Api 里有重名方法，后定义的会覆盖前面的：\n" + "\n".join(duplicates),
        )


class DialogHelperTest(unittest.TestCase):
    """两个保存对话框必须各自独立存在。"""

    def test_duty_and_pack_save_dialogs_are_distinct(self):
        duty = getattr(Api, "_ask_duty_save_path", None)
        pack = getattr(Api, "_ask_pack_save_path", None)

        self.assertIsNotNone(duty, "值班表保存对话框不见了")
        self.assertIsNotNone(pack, "数据包保存对话框不见了")
        self.assertIsNot(duty, pack, "两个保存对话框不能是同一个函数")

    def test_duty_save_dialog_takes_week(self):
        params = list(inspect.signature(Api._ask_duty_save_path).parameters)
        self.assertEqual(params, ["self", "week"])

    def test_pack_save_dialog_takes_name_and_types(self):
        params = list(inspect.signature(Api._ask_pack_save_path).parameters)
        self.assertEqual(params, ["default_name", "filetypes"])


if __name__ == "__main__":
    unittest.main()
