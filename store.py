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
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS seasons ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,"
            " created_at TEXT NOT NULL, ranking TEXT NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS used_topic_keys ("
            " guild_id INTEGER NOT NULL, topic_key TEXT NOT NULL,"
            " PRIMARY KEY (guild_id, topic_key))")
        self.conn.commit()

    def used_topics(self, guild_id: int) -> set[str]:
        """ワードウルフで、このサーバーで一度出たお題の名前。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT topic_key FROM used_topic_keys WHERE guild_id=?", (guild_id,)).fetchall()
        return {r[0] for r in rows}

    def mark_topic_used(self, guild_id: int, topic_key: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO used_topic_keys (guild_id, topic_key) VALUES (?, ?)", (guild_id, topic_key))
            self.conn.commit()

    def reset_topics(self, guild_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM used_topic_keys WHERE guild_id=?", (guild_id,))
            self.conn.commit()

    def reset_season(self, guild_id: int, record: bool) -> int:
        """全員の残高を初期値に戻す。record が True なら、リセット前のランキングを seasons に残す。戻り値は人数。"""
        import datetime
        import json
        with self._lock:
            rows = self.conn.execute(
                "SELECT user_id, balance FROM balances WHERE guild_id=? ORDER BY balance DESC",
                (guild_id,)).fetchall()
            if record:
                ranking = json.dumps([{"user_id": u, "balance": b} for u, b in rows], ensure_ascii=False)
                now = datetime.datetime.now().isoformat(timespec="seconds")
                self.conn.execute(
                    "INSERT INTO seasons (guild_id, created_at, ranking) VALUES (?, ?, ?)",
                    (guild_id, now, ranking))
            self.conn.execute(
                "UPDATE balances SET balance=? WHERE guild_id=?", (START_POINTS, guild_id))
            self.conn.commit()
        return len(rows)

    def balance(self, guild_id: int, user_id: int) -> int:
        if user_id < 0:                          # テスト用の人は、ポイントを保存しない
            return START_POINTS
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
        if user_id < 0:
            return START_POINTS
        new = self.balance(guild_id, user_id) + delta
        with self._lock:
            self.conn.execute(
                "UPDATE balances SET balance=? WHERE guild_id=? AND user_id=?", (new, guild_id, user_id))
            self.conn.commit()
        return new
