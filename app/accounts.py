"""账号数据的 SQLite 持久化存储。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountStore:
    """账号存储，密码通过注入的加密器加密后再落盘。"""

    def __init__(self, db_path: str | Path, encryptor) -> None:
        self._encryptor = encryptor
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT NOT NULL,
                username TEXT NOT NULL,
                password_enc BLOB NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_used_at TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def add_account(
        self, nickname: str, username: str, password: str, notes: str = ""
    ) -> int:
        encrypted = self._encryptor.encrypt(password.encode("utf-8"))
        cursor = self._conn.execute(
            "INSERT INTO accounts (nickname, username, password_enc, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nickname, username, encrypted, notes, _utcnow()),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def update_account(
        self,
        account_id: int,
        *,
        nickname: str | None = None,
        username: str | None = None,
        password: str | None = None,
        notes: str | None = None,
    ) -> None:
        fields: dict[str, object] = {}
        if nickname is not None:
            fields["nickname"] = nickname
        if username is not None:
            fields["username"] = username
        if notes is not None:
            fields["notes"] = notes
        if password is not None:
            fields["password_enc"] = self._encryptor.encrypt(password.encode("utf-8"))
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self._conn.execute(
            f"UPDATE accounts SET {assignments} WHERE id = ?",
            (*fields.values(), account_id),
        )
        self._conn.commit()

    def delete_account(self, account_id: int) -> None:
        self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        if self._get_active_account_id() == account_id:
            self._conn.execute("DELETE FROM settings WHERE key = 'active_account_id'")
        self._conn.commit()

    def list_accounts(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, nickname, username, notes, created_at, last_used_at "
            "FROM accounts ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_account(self, account_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, nickname, username, notes, created_at, last_used_at "
            "FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_password(self, account_id: int) -> str:
        row = self._conn.execute(
            "SELECT password_enc FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"账号 {account_id} 不存在")
        return self._encryptor.decrypt(bytes(row["password_enc"])).decode("utf-8")

    def set_active_account_id(self, account_id: int | None) -> None:
        if account_id is None:
            self._conn.execute("DELETE FROM settings WHERE key = 'active_account_id'")
        else:
            if self.get_account(account_id) is None:
                raise KeyError(f"账号 {account_id} 不存在")
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES ('active_account_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(account_id),),
            )
            self._conn.execute(
                "UPDATE accounts SET last_used_at = ? WHERE id = ?",
                (_utcnow(), account_id),
            )
        self._conn.commit()

    def get_active_account_id(self) -> int | None:
        account_id = self._get_active_account_id()
        if account_id is not None and self.get_account(account_id) is None:
            self._conn.execute("DELETE FROM settings WHERE key = 'active_account_id'")
            self._conn.commit()
            return None
        return account_id

    def _get_active_account_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = 'active_account_id'"
        ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
