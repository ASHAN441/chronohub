"""AccountStore 与 DPAPI 加解密的单元测试。"""

from __future__ import annotations

import sqlite3
import shutil
import unittest
import uuid
from pathlib import Path

from app.security import DpapiEncryptor
from app.storage import AccountStore


class DpapiEncryptorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.encryptor = DpapiEncryptor()

    def test_roundtrip_ascii(self) -> None:
        original = b"plain-text-password"
        self.assertEqual(self.encryptor.decrypt(self.encryptor.encrypt(original)), original)

    def test_roundtrip_unicode(self) -> None:
        original = "中文密码：p@ssw0rd!".encode("utf-8")
        self.assertEqual(self.encryptor.decrypt(self.encryptor.encrypt(original)), original)


class AccountStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.tmp_path = project_root / f"test_tmp_{uuid.uuid4().hex}"
        self.tmp_path.mkdir(parents=True, exist_ok=False)
        self.db_path = self.tmp_path / "test.db"
        self.store = AccountStore(self.db_path, DpapiEncryptor())

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.tmp_path, ignore_errors=True)

    def test_add_and_list_account(self) -> None:
        account_id = self.store.add_account("张三", "zhangsan", "secret123", "一班")
        accounts = self.store.list_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["id"], account_id)
        self.assertEqual(accounts[0]["nickname"], "张三")
        self.assertEqual(accounts[0]["username"], "zhangsan")
        self.assertNotIn("password", accounts[0])

    def test_password_roundtrip(self) -> None:
        account_id = self.store.add_account("李四", "lisi", "p@ssw0rd")
        self.assertEqual(self.store.get_password(account_id), "p@ssw0rd")

    def test_password_is_encrypted_at_rest(self) -> None:
        account_id = self.store.add_account("王五", "wangwu", "plaintext-password")
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT password_enc FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        finally:
            connection.close()
        self.assertNotIn(b"plaintext-password", row[0])

    def test_update_account(self) -> None:
        account_id = self.store.add_account("旧昵称", "old", "pw")
        self.store.update_account(account_id, nickname="新昵称", password="new-pw")
        accounts = self.store.list_accounts()
        self.assertEqual(accounts[0]["nickname"], "新昵称")
        self.assertEqual(self.store.get_password(account_id), "new-pw")

    def test_delete_account(self) -> None:
        account_id = self.store.add_account("临时", "tmp", "pw")
        self.store.delete_account(account_id)
        self.assertEqual(self.store.list_accounts(), [])
        with self.assertRaises(KeyError):
            self.store.get_password(account_id)

    def test_active_account(self) -> None:
        first = self.store.add_account("A", "ua", "pw")
        second = self.store.add_account("B", "ub", "pw")
        self.assertIsNone(self.store.get_active_account_id())
        self.store.set_active_account_id(first)
        self.assertEqual(self.store.get_active_account_id(), first)
        self.store.set_active_account_id(second)
        self.assertEqual(self.store.get_active_account_id(), second)

    def test_delete_active_account_clears_active(self) -> None:
        account_id = self.store.add_account("A", "ua", "pw")
        self.store.set_active_account_id(account_id)
        self.store.delete_account(account_id)
        self.assertIsNone(self.store.get_active_account_id())

    def test_set_active_nonexistent_account_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.set_active_account_id(999)

    def test_settings_roundtrip(self) -> None:
        self.assertIsNone(self.store.get_setting("login_url"))
        self.store.set_setting("login_url", "http://example.com")
        self.assertEqual(self.store.get_setting("login_url"), "http://example.com")
        self.store.set_setting("login_url", "http://example.com/2")
        self.assertEqual(self.store.get_setting("login_url"), "http://example.com/2")


if __name__ == "__main__":
    unittest.main()
