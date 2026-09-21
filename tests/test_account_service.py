"""账号服务与 js_api 账号接口的单元测试。

使用临时目录，不触碰真实的 data/ 数据。
"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import Api  # noqa: E402
from app.account_service import AccountService  # noqa: E402


class AccountServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "accounts.db")
        self.service = AccountService(self.db)

    def tearDown(self):
        self.service.close()
        self._tmp.cleanup()

    def test_add_and_list(self):
        result = self.service.add("张三", "2023010101", "pwd123", "一班")
        self.assertTrue(result["ok"], result)
        rows = self.service.list_accounts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["nickname"], "张三")
        self.assertEqual(rows[0]["username"], "2023010101")
        self.assertFalse(rows[0]["imported"])
        self.assertFalse(rows[0]["active"])

    def test_password_roundtrip(self):
        account_id = self.service.add("张三", "2023010101", "pwd123")["id"]
        got = self.service.get_password(account_id)
        self.assertTrue(got["ok"])
        self.assertEqual(got["password"], "pwd123")

    def test_add_rejects_empty_fields(self):
        self.assertFalse(self.service.add("", "u", "p")["ok"])
        self.assertFalse(self.service.add("n", "", "p")["ok"])
        self.assertFalse(self.service.add("n", "u", "")["ok"])

    def test_set_active(self):
        account_id = self.service.add("张三", "2023010101", "pwd")["id"]
        self.assertTrue(self.service.set_active(account_id)["ok"])
        rows = self.service.list_accounts()
        self.assertTrue(rows[0]["active"])
        self.assertEqual(self.service.get_active_account_id(), account_id)

    def test_update_keeps_password_when_blank(self):
        account_id = self.service.add("张三", "u1", "oldpwd")["id"]
        self.assertTrue(self.service.update(account_id, nickname="李四", notes="改过")["ok"])
        rows = self.service.list_accounts()
        self.assertEqual(rows[0]["nickname"], "李四")
        self.assertEqual(rows[0]["notes"], "改过")
        # 密码留空表示不改
        self.assertEqual(self.service.get_password(account_id)["password"], "oldpwd")

    def test_update_password(self):
        account_id = self.service.add("张三", "u1", "oldpwd")["id"]
        self.service.update(account_id, password="newpwd")
        self.assertEqual(self.service.get_password(account_id)["password"], "newpwd")

    def test_update_missing_account(self):
        self.assertFalse(self.service.update(9999, nickname="x")["ok"])

    def test_remove(self):
        account_id = self.service.add("张三", "u1", "p")["id"]
        self.assertTrue(self.service.remove(account_id)["ok"])
        self.assertEqual(self.service.list_accounts(), [])

    def test_imported_flag_reflects_student_ids(self):
        self.service.add("张三", "2023010101", "p")
        self.service.add("李四", "2023010102", "p")
        rows = self.service.list_accounts(imported_ids={"2023010102"})
        by_user = {r["username"]: r["imported"] for r in rows}
        self.assertFalse(by_user["2023010101"])
        self.assertTrue(by_user["2023010102"])

    def test_login_url_setting(self):
        self.assertEqual(self.service.get_login_url("fallback"), "fallback")
        self.service.set_login_url("http://example.com/login")
        self.assertEqual(
            self.service.get_login_url("fallback"), "http://example.com/login"
        )


class ApiAccountBridgeTest(unittest.TestCase):
    """验证 api.py 暴露给前端的账号接口。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.api = Api(
            data_path=os.path.join(root, "people.json"),
            groups_path=os.path.join(root, "groups.json"),
            accounts_db_path=os.path.join(root, "accounts.db"),
        )

    def tearDown(self):
        self.api.accounts.close()
        self._tmp.cleanup()

    def test_empty_state(self):
        payload = self.api.get_accounts()
        self.assertEqual(payload["accounts"], [])
        self.assertIsNone(payload["active_id"])

    def test_crud_via_bridge(self):
        payload = self.api.add_account("张三", "2023010101", "pwd", "一班")
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(len(payload["accounts"]), 1)

        account_id = payload["accounts"][0]["id"]
        payload = self.api.set_active_account(account_id)
        self.assertTrue(payload["active_id"] == account_id)

        payload = self.api.delete_account(account_id)
        self.assertEqual(payload["accounts"], [])

    def test_password_never_in_list_payload(self):
        self.api.add_account("张三", "2023010101", "secret")
        payload = self.api.get_accounts()
        self.assertNotIn("password", payload["accounts"][0])

    def test_system_urls(self):
        info = self.api.get_system_urls()
        self.assertGreaterEqual(len(info["urls"]), 2)
        self.assertTrue(info["current"].startswith("http"))

    def test_custom_login_url_persists(self):
        self.api.set_login_url("http://10.0.0.1/jwglxt/login_slogin.html")
        self.assertEqual(
            self.api.get_system_urls()["current"],
            "http://10.0.0.1/jwglxt/login_slogin.html",
        )


class CrossThreadTest(unittest.TestCase):
    """账号库必须支持跨线程访问。

    这是界面冒烟测试里真实踩到的 bug：pywebview 的 js_api 方法运行在子线程，
    而 AccountStore 原实现在主线程建连接，子线程一调用就报
    "SQLite objects created in a thread can only be used in that same thread"。
    修法是连接改为 thread-local。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "accounts.db")
        self.service = AccountService(self.db)

    def tearDown(self):
        self.service.close()
        self._tmp.cleanup()

    def test_write_and_read_from_worker_thread(self):
        results = {}
        errors = []

        def worker():
            try:
                results["add"] = self.service.add("线程测试", "88880001", "pwd")
                results["list"] = self.service.list_accounts()
                results["password"] = self.service.get_password(results["add"]["id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertEqual(errors, [])
        self.assertTrue(results["add"]["ok"], results["add"])
        self.assertEqual(len(results["list"]), 1)
        self.assertEqual(results["list"][0]["nickname"], "线程测试")
        self.assertEqual(results["password"]["password"], "pwd")

    def test_main_thread_sees_worker_writes(self):
        def worker():
            self.service.add("线程测试", "88880001", "pwd")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        rows = self.service.list_accounts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "88880001")

    def test_db_file_released_after_close(self):
        """close() 之后数据库文件必须能被删除，否则说明连接句柄泄漏了。

        这是踩过的坑：子线程创建的连接若没在 close() 里真正关闭，
        文件会一直被占用（Windows 上抛 PermissionError）。
        """

        def worker():
            self.service.add("线程测试", "88880001", "pwd")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.service.close()
        os.unlink(self.db)
        self.assertFalse(os.path.exists(self.db))


if __name__ == "__main__":
    unittest.main()
