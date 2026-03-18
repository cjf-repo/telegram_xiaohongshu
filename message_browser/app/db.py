"""Database utilities for message browser."""

import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings


class Database:
    """Simple DB wrapper supporting MySQL and SQLite."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.adapter = settings.db_adapter
        self.placeholder = "%s" if self.adapter == "mysql" else "?"

    @contextmanager
    def connection(self):
        """Open and close a DB connection."""
        conn = None
        try:
            if self.adapter == "mysql":
                conn = pymysql.connect(
                    host=self.settings.mysql_host,
                    port=self.settings.mysql_port,
                    user=self.settings.mysql_user,
                    password=self.settings.mysql_password,
                    database=self.settings.mysql_database,
                    charset=self.settings.mysql_charset,
                    cursorclass=DictCursor,
                    autocommit=True,
                )
            else:
                conn = sqlite3.connect(self.settings.sqlite_path)
                conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if conn is not None:
                conn.close()

    def fetch_all(self, sql: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
        """Fetch all rows as dict list."""
        params = tuple(params or [])
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        if self.adapter == "sqlite":
            return [dict(row) for row in rows]
        return list(rows)

    def fetch_one(self, sql: str, params: Optional[Iterable[Any]] = None) -> Optional[Dict[str, Any]]:
        """Fetch one row as dict."""
        rows = self.fetch_all(sql, params)
        if not rows:
            return None
        return rows[0]

    @staticmethod
    def to_datetime_str(value: Any) -> Optional[str]:
        """Convert date/datetime to normalized string."""
        if value is None:
            return None
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        value_str = str(value)
        if len(value_str) >= 19:
            return value_str[:19]
        return value_str

    @staticmethod
    def normalize_chat_id(value: Any) -> str:
        """Normalize chat id."""
        return str(value) if value is not None else ""

    def build_or_pairs(
        self,
        pairs: List[Tuple[str, int]],
        chat_field: str,
        anchor_expr: str,
    ) -> Tuple[str, List[Any]]:
        """Build OR conditions for (chat_id, anchor_id) pairs."""
        conds: List[str] = []
        params: List[Any] = []
        for chat_id, anchor_id in pairs:
            conds.append(
                f"({chat_field}={self.placeholder} AND {anchor_expr}={self.placeholder})"
            )
            params.extend([chat_id, anchor_id])
        if not conds:
            return "1=0", []
        return " OR ".join(conds), params
