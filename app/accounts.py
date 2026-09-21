"""账号数据的 SQLite 持久化存储。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountStore:
    """账号存储，密码通过注入的加密器加密后再落盘。

    连接按线程惰性创建（thread-local）。原因：pywebview 的 js_api 方法运行在
    子线程，而 SQLite 连接默认禁止跨线程使用（check_same_thread）。每个线程
    独占一条连接最省心，也不用在业务代码里到处加锁。

    所有创建出来的连接都被登记在 _conns 里，close() 时统一关闭——否则子线程
    的连接会一直占着文件句柄（实测会导致测试的临时目录删不掉）。

    connect 时传 check_same_thread=False 是必需的：close() 通常在主线程执行，
    而那条连接是在子线程创建的，默认设置下连 close() 都会抛 ProgrammingError。
    因为连接本身是 thread-local 的，每个线程仍独占自己那条，不存在并发共用。
    """

    def __init__(self, db_path: str | Path, encryptor) -> None:
        self._encryptor = encryptor
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._init_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn


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
        """关闭所有线程创建过的连接（用于程序退出与测试清理）。"""
        with self._conns_lock:
            conns, self._conns = list(self._conns), []
        for conn in conns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if getattr(self._local, "conn", None) is not None:
            self._local.conn = None
