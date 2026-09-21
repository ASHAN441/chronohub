"""账号管理服务。

把 AccountStore（SQLite）与 DpapiEncryptor（Windows DPAPI）封装成前端友好的结构，
并负责判断每个账号的「课表是否已入库」——这是合并后两个模块的关联点。

独立于 pywebview，便于直接做单元测试。
"""

from __future__ import annotations

import os

from .accounts import AccountStore
from .paths import data_dir
from .security import DpapiEncryptor, EncryptionError

SETTING_LOGIN_URL = "login_url"


def default_db_path() -> str:
    """账号库默认放在 data/accounts.db（打包后位于 exe 同级目录）。

    必须走 paths.data_dir()：用 __file__ 推算的话，onefile 打包后会落到临时
    解压目录，重启即丢——**用户存的账号全没了**。这个 bug 是打包后实测
    「exe 同级只生成了 data/webview，没有 accounts.db」才发现的。
    """
    return os.path.join(data_dir(), "accounts.db")


class AccountService:
    """账号的增删改查 + 凭据解密。所有对外方法都不抛异常，改以 ok/error 返回。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self._store = AccountStore(self.db_path, DpapiEncryptor())

    # ---------- 查询 ----------

    def list_accounts(self, imported_ids: set | None = None) -> list:
        """返回账号列表。

        imported_ids 是已导入课表的人员学号集合，用来标记「课表已入库」。
        """
        imported_ids = imported_ids or set()
        active_id = self._store.get_active_account_id()
        out = []
        for row in self._store.list_accounts():
            out.append(
                {
                    "id": row["id"],
                    "nickname": row["nickname"],
                    "username": row["username"],
                    "notes": row["notes"],
                    "active": row["id"] == active_id,
                    "imported": row["username"] in imported_ids,
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                }
            )
        return out

    def get_active_account_id(self) -> int | None:
        return self._store.get_active_account_id()

    def get_login_url(self, default: str) -> str:
        return self._store.get_setting(SETTING_LOGIN_URL, default) or default

    def set_login_url(self, url: str) -> None:
        self._store.set_setting(SETTING_LOGIN_URL, url or "")

    # ---------- 凭据 ----------

    def get_password(self, account_id: int) -> dict:
        """取出明文密码。仅用于「复制密码」与自动登录，不落前端存储。"""
        try:
            password = self._store.get_password(int(account_id))
        except KeyError:
            return {"ok": False, "error": "账号不存在"}
        except EncryptionError as exc:
            return {"ok": False, "error": f"无法解密密码：{exc}"}
        return {"ok": True, "password": password}

    # ---------- 写入 ----------

    def add(self, nickname: str, username: str, password: str, notes: str = "") -> dict:
        nickname = (nickname or "").strip()
        username = (username or "").strip()
        if not nickname or not username or not password:
            return {"ok": False, "error": "昵称、用户名和密码不能为空"}
        try:
            account_id = self._store.add_account(nickname, username, password, notes or "")
        except EncryptionError as exc:
            return {"ok": False, "error": f"密码加密失败：{exc}"}
        return {"ok": True, "id": account_id}

    def update(
        self,
        account_id: int,
        nickname: str = "",
        username: str = "",
        password: str = "",
        notes: str = "",
    ) -> dict:
        """更新账号。password 留空表示不修改密码。"""
        account_id = int(account_id)
        if self._store.get_account(account_id) is None:
            return {"ok": False, "error": "账号不存在"}
        fields: dict = {}
        if nickname.strip():
            fields["nickname"] = nickname.strip()
        if username.strip():
            fields["username"] = username.strip()
        fields["notes"] = notes or ""
        if password:
            fields["password"] = password
        try:
            self._store.update_account(account_id, **fields)
        except EncryptionError as exc:
            return {"ok": False, "error": f"密码加密失败：{exc}"}
        return {"ok": True}

    def remove(self, account_id: int) -> dict:
        try:
            self._store.delete_account(int(account_id))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def set_active(self, account_id: int) -> dict:
        try:
            self._store.set_active_account_id(int(account_id))
        except KeyError:
            return {"ok": False, "error": "账号不存在"}
        return {"ok": True}

    def close(self) -> None:
        self._store.close()
