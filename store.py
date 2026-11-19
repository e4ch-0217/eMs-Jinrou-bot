"""ポイントの保存(SQLite)。サーバーごと・ユーザーごとに残高を持ちます。"""
from __future__ import annotations

import os
import sqlite3
import threading

from points import START_POINTS

DB_PATH = os.getenv("DB_PATH", "jinro.db")


class Store:
    def __init__(self, path: str = DB_PATH):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS balances ("
            " guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, balance INTEGER NOT NULL,"
            " PRIMARY KEY (guild_id, user_id))")
        self.conn.commit()

    def balance(self, guild_id: int, user_id: int) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT balance FROM balances WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
            if row is not None:
                return row[0]
            self.conn.execute(
                "INSERT INTO balances (guild_id, user_id, balance) VALUES (?, ?, ?)",
                (guild_id, user_id, START_POINTS))
            self.conn.commit()
            return START_POINTS

    def add(self, guild_id: int, user_id: int, delta: int) -> int:
        """残高を増減して、新しい残高を返す。"""
        new = self.balance(guild_id, user_id) + delta
        with self._lock:
            self.conn.execute(
                "UPDATE balances SET balance=? WHERE guild_id=? AND user_id=?", (new, guild_id, user_id))
            self.conn.commit()
        return new
