"""Message metadata index database."""

import os
import re
import sqlite3
from datetime import datetime
from threading import Lock
from typing import Any, Dict, Optional

from loguru import logger


class MessageIndexDB:
    """Store message/media mapping in sqlite or mysql."""

    def __init__(
        self,
        adapter: str = "sqlite",
        sqlite_db_path: str = "",
        mysql_config: Optional[dict] = None,
    ):
        self.adapter = (adapter or "sqlite").lower()
        if self.adapter not in ("sqlite", "mysql"):
            raise ValueError(f"unsupported message_db adapter: {adapter}")

        self.sqlite_db_path = sqlite_db_path or os.path.join(
            os.path.abspath("."), "metadata.db"
        )
        self.mysql_config = mysql_config or {}

        self._lock = Lock()
        self.table_prefix = self._normalize_table_prefix(
            self.mysql_config.get("table_prefix", "")
        )
        self.messages_table_name = f"{self.table_prefix}messages"
        self.media_files_table_name = f"{self.table_prefix}media_files"
        self.message_links_table_name = f"{self.table_prefix}message_links"

        self.messages_table = f"`{self.messages_table_name}`"
        self.media_files_table = f"`{self.media_files_table_name}`"
        self.message_links_table = f"`{self.message_links_table_name}`"

        if self.adapter == "sqlite":
            self._ensure_parent_dir()

        self._init_db()

    @staticmethod
    def _normalize_table_prefix(prefix: str) -> str:
        """Normalize table prefix."""
        if not prefix:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_]+", prefix):
            raise ValueError("message_db.mysql.table_prefix only supports [A-Za-z0-9_]")
        return prefix

    def _ensure_parent_dir(self):
        parent = os.path.dirname(os.path.abspath(self.sqlite_db_path))
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

    @staticmethod
    def _now():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _load_pymysql(self):
        """Load pymysql lazily so sqlite mode doesn't require it."""
        try:
            import pymysql  # type: ignore
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "PyMySQL not found. Please install dependency `PyMySQL`."
            ) from e
        return pymysql

    def _connect_sqlite(self):
        conn = sqlite3.connect(self.sqlite_db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _mysql_base_config(self) -> dict:
        """Build mysql connect config."""
        host = self.mysql_config.get("host", "127.0.0.1")
        user = self.mysql_config.get("user", "")
        password = self.mysql_config.get("password", "")
        charset = self.mysql_config.get("charset", "utf8mb4")

        host = str(host) if host is not None else "127.0.0.1"
        user = str(user) if user is not None else ""
        password = str(password) if password is not None else ""
        charset = str(charset) if charset is not None else "utf8mb4"

        return {
            "host": host,
            "port": int(self.mysql_config.get("port", 3306)),
            "user": user,
            "password": password,
            "charset": charset,
            "autocommit": True,
            "connect_timeout": int(self.mysql_config.get("connect_timeout", 10)),
        }

    def _connect_mysql(self, with_database: bool = True):
        pymysql = self._load_pymysql()
        cfg = self._mysql_base_config()
        if with_database:
            database = self.mysql_config.get("database", "")
            if not database:
                raise ValueError("message_db.mysql.database is required")
            cfg["database"] = str(database)
        return pymysql.connect(**cfg)

    def _init_db(self):
        if self.adapter == "mysql":
            self._init_mysql_db()
            return
        self._init_sqlite_db()

    def _init_sqlite_db(self):
        with self._lock:
            with self._connect_sqlite() as conn:
                conn.executescript(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.messages_table} (
                        chat_id TEXT NOT NULL,
                        message_id INTEGER NOT NULL,
                        message_date TEXT,
                        sender_id INTEGER,
                        sender_name TEXT,
                        message_text TEXT,
                        message_caption TEXT,
                        media_group_id TEXT,
                        reply_to_message_id INTEGER,
                        message_thread_id INTEGER,
                        has_media INTEGER NOT NULL DEFAULT 0,
                        media_type TEXT,
                        download_status TEXT,
                        is_separator INTEGER NOT NULL DEFAULT 0,
                        separator_reason TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(chat_id, message_id)
                    );

                    CREATE TABLE IF NOT EXISTS {self.media_files_table} (
                        chat_id TEXT NOT NULL,
                        message_id INTEGER NOT NULL,
                        media_type TEXT,
                        telegram_file_id TEXT,
                        telegram_file_unique_id TEXT,
                        original_file_name TEXT,
                        saved_file_path TEXT,
                        saved_file_size INTEGER,
                        download_status TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(chat_id, message_id),
                        FOREIGN KEY(chat_id, message_id)
                            REFERENCES {self.messages_table}(chat_id, message_id)
                            ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS {self.message_links_table} (
                        chat_id TEXT NOT NULL,
                        media_message_id INTEGER NOT NULL,
                        text_message_id INTEGER NOT NULL,
                        link_type TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(chat_id, media_message_id, text_message_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_messages_media_group_id
                    ON {self.messages_table}(chat_id, media_group_id);

                    CREATE INDEX IF NOT EXISTS idx_messages_is_separator
                    ON {self.messages_table}(chat_id, is_separator);

                    CREATE INDEX IF NOT EXISTS idx_message_links_text_id
                    ON {self.message_links_table}(chat_id, text_message_id);
                    """
                )
                self._ensure_sqlite_schema(conn)

    def _ensure_sqlite_schema(self, conn: sqlite3.Connection):
        """Migrate legacy sqlite schema."""
        self._ensure_sqlite_column(
            conn, self.messages_table_name, "is_separator", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_sqlite_column(
            conn, self.messages_table_name, "separator_reason", "TEXT"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_messages_is_separator ON {self.messages_table}(chat_id, is_separator)"
        )

    def _ensure_sqlite_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_sql: str,
    ):
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info(`{table_name}`)").fetchall()
        }
        if column_name not in columns:
            conn.execute(
                f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_sql}"
            )

    def _init_mysql_db(self):
        database = self.mysql_config.get("database", "")
        if not database:
            raise ValueError("message_db.mysql.database is required")
        database = str(database)
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise ValueError("message_db.mysql.database only supports [A-Za-z0-9_]")

        charset = str(self.mysql_config.get("charset", "utf8mb4"))
        if not re.fullmatch(r"[A-Za-z0-9_]+", charset):
            raise ValueError("message_db.mysql.charset only supports [A-Za-z0-9_]")

        with self._lock:
            with self._connect_mysql(with_database=False) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET {charset}"
                    )

            with self._connect_mysql(with_database=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {self.messages_table} (
                            chat_id VARCHAR(64) NOT NULL,
                            message_id BIGINT NOT NULL,
                            message_date DATETIME NULL,
                            sender_id BIGINT NULL,
                            sender_name VARCHAR(255) NULL,
                            message_text LONGTEXT NULL,
                            message_caption LONGTEXT NULL,
                            media_group_id VARCHAR(64) NULL,
                            reply_to_message_id BIGINT NULL,
                            message_thread_id BIGINT NULL,
                            has_media TINYINT(1) NOT NULL DEFAULT 0,
                            media_type VARCHAR(32) NULL,
                            download_status VARCHAR(32) NULL,
                            is_separator TINYINT(1) NOT NULL DEFAULT 0,
                            separator_reason VARCHAR(255) NULL,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL,
                            PRIMARY KEY(chat_id, message_id),
                            INDEX idx_messages_media_group_id(chat_id, media_group_id),
                            INDEX idx_messages_is_separator(chat_id, is_separator)
                        ) ENGINE=InnoDB DEFAULT CHARSET={charset}
                        """
                    )

                    cursor.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {self.media_files_table} (
                            chat_id VARCHAR(64) NOT NULL,
                            message_id BIGINT NOT NULL,
                            media_type VARCHAR(32) NULL,
                            telegram_file_id VARCHAR(255) NULL,
                            telegram_file_unique_id VARCHAR(255) NULL,
                            original_file_name TEXT NULL,
                            saved_file_path TEXT NULL,
                            saved_file_size BIGINT NULL,
                            download_status VARCHAR(32) NULL,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL,
                            PRIMARY KEY(chat_id, message_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET={charset}
                        """
                    )

                    cursor.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {self.message_links_table} (
                            chat_id VARCHAR(64) NOT NULL,
                            media_message_id BIGINT NOT NULL,
                            text_message_id BIGINT NOT NULL,
                            link_type VARCHAR(32) NOT NULL,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL,
                            PRIMARY KEY(chat_id, media_message_id, text_message_id),
                            INDEX idx_message_links_text_id(chat_id, text_message_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET={charset}
                        """
                    )
                    self._ensure_mysql_schema(cursor, database)

    def _ensure_mysql_schema(self, cursor, database: str):
        """Migrate legacy mysql schema."""
        self._ensure_mysql_column(
            cursor,
            database,
            self.messages_table_name,
            "is_separator",
            "TINYINT(1) NOT NULL DEFAULT 0",
        )
        self._ensure_mysql_column(
            cursor,
            database,
            self.messages_table_name,
            "separator_reason",
            "VARCHAR(255) NULL",
        )
        self._ensure_mysql_index(
            cursor,
            self.messages_table_name,
            "idx_messages_is_separator",
            "(chat_id, is_separator)",
        )

    def _ensure_mysql_column(
        self,
        cursor,
        database: str,
        table_name: str,
        column_name: str,
        column_sql: str,
    ):
        cursor.execute(
            """
            SELECT COUNT(1)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s
            """,
            (database, table_name, column_name),
        )
        row = cursor.fetchone()
        if row and int(row[0]) == 0:
            cursor.execute(
                f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_sql}"
            )

    def _ensure_mysql_index(
        self,
        cursor,
        table_name: str,
        index_name: str,
        index_fields_sql: str,
    ):
        cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name=%s", (index_name,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                f"CREATE INDEX `{index_name}` ON `{table_name}` {index_fields_sql}"
            )

    def upsert_message(self, record: Dict[str, Any]):
        """Upsert message/media mapping."""
        now = self._now()
        if self.adapter == "mysql":
            self._upsert_message_mysql(record, now)
            return
        self._upsert_message_sqlite(record, now)

    def _upsert_message_sqlite(self, record: Dict[str, Any], now: str):
        with self._lock:
            with self._connect_sqlite() as conn:
                conn.execute(
                    f"""
                    INSERT INTO {self.messages_table}(
                        chat_id,
                        message_id,
                        message_date,
                        sender_id,
                        sender_name,
                        message_text,
                        message_caption,
                        media_group_id,
                        reply_to_message_id,
                        message_thread_id,
                        has_media,
                        media_type,
                        download_status,
                        is_separator,
                        separator_reason,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, message_id) DO UPDATE SET
                        message_date=excluded.message_date,
                        sender_id=excluded.sender_id,
                        sender_name=excluded.sender_name,
                        message_text=excluded.message_text,
                        message_caption=excluded.message_caption,
                        media_group_id=excluded.media_group_id,
                        reply_to_message_id=excluded.reply_to_message_id,
                        message_thread_id=excluded.message_thread_id,
                        has_media=excluded.has_media,
                        media_type=excluded.media_type,
                        download_status=excluded.download_status,
                        is_separator=excluded.is_separator,
                        separator_reason=excluded.separator_reason,
                        updated_at=excluded.updated_at
                    """,
                    (
                        record["chat_id"],
                        record["message_id"],
                        record["message_date"],
                        record["sender_id"],
                        record["sender_name"],
                        record["message_text"],
                        record["message_caption"],
                        record["media_group_id"],
                        record["reply_to_message_id"],
                        record["message_thread_id"],
                        record["has_media"],
                        record["media_type"],
                        record["download_status"],
                        record["is_separator"],
                        record["separator_reason"],
                        now,
                        now,
                    ),
                )

                if not record["has_media"]:
                    conn.execute(
                        f"DELETE FROM {self.media_files_table} WHERE chat_id=? AND message_id=?",
                        (record["chat_id"], record["message_id"]),
                    )
                    conn.execute(
                        f"DELETE FROM {self.message_links_table} WHERE chat_id=? AND media_message_id=?",
                        (record["chat_id"], record["message_id"]),
                    )
                    return

                conn.execute(
                    f"""
                    INSERT INTO {self.media_files_table}(
                        chat_id,
                        message_id,
                        media_type,
                        telegram_file_id,
                        telegram_file_unique_id,
                        original_file_name,
                        saved_file_path,
                        saved_file_size,
                        download_status,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, message_id) DO UPDATE SET
                        media_type=excluded.media_type,
                        telegram_file_id=excluded.telegram_file_id,
                        telegram_file_unique_id=excluded.telegram_file_unique_id,
                        original_file_name=excluded.original_file_name,
                        saved_file_path=COALESCE(NULLIF(excluded.saved_file_path, ''), saved_file_path),
                        saved_file_size=CASE
                            WHEN COALESCE(NULLIF(excluded.saved_file_path, ''), '') = '' THEN saved_file_size
                            ELSE excluded.saved_file_size
                        END,
                        download_status=excluded.download_status,
                        updated_at=excluded.updated_at
                    """,
                    (
                        record["chat_id"],
                        record["message_id"],
                        record["media_type"],
                        record["telegram_file_id"],
                        record["telegram_file_unique_id"],
                        record["original_file_name"],
                        record["saved_file_path"],
                        record["saved_file_size"],
                        record["download_status"],
                        now,
                        now,
                    ),
                )

                self._upsert_message_links_sqlite(conn, record, now)

    def _upsert_message_mysql(self, record: Dict[str, Any], now: str):
        with self._lock:
            with self._connect_mysql(with_database=True) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {self.messages_table}(
                            chat_id,
                            message_id,
                            message_date,
                            sender_id,
                            sender_name,
                            message_text,
                            message_caption,
                            media_group_id,
                            reply_to_message_id,
                            message_thread_id,
                            has_media,
                            media_type,
                            download_status,
                            is_separator,
                            separator_reason,
                            created_at,
                            updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            message_date=VALUES(message_date),
                            sender_id=VALUES(sender_id),
                            sender_name=VALUES(sender_name),
                            message_text=VALUES(message_text),
                            message_caption=VALUES(message_caption),
                            media_group_id=VALUES(media_group_id),
                            reply_to_message_id=VALUES(reply_to_message_id),
                            message_thread_id=VALUES(message_thread_id),
                            has_media=VALUES(has_media),
                            media_type=VALUES(media_type),
                            download_status=VALUES(download_status),
                            is_separator=VALUES(is_separator),
                            separator_reason=VALUES(separator_reason),
                            updated_at=VALUES(updated_at)
                        """,
                        (
                            record["chat_id"],
                            record["message_id"],
                            record["message_date"],
                            record["sender_id"],
                            record["sender_name"],
                            record["message_text"],
                            record["message_caption"],
                            record["media_group_id"],
                            record["reply_to_message_id"],
                            record["message_thread_id"],
                            record["has_media"],
                            record["media_type"],
                            record["download_status"],
                            record["is_separator"],
                            record["separator_reason"],
                            now,
                            now,
                        ),
                    )

                    if not record["has_media"]:
                        cursor.execute(
                            f"DELETE FROM {self.media_files_table} WHERE chat_id=%s AND message_id=%s",
                            (record["chat_id"], record["message_id"]),
                        )
                        cursor.execute(
                            f"DELETE FROM {self.message_links_table} WHERE chat_id=%s AND media_message_id=%s",
                            (record["chat_id"], record["message_id"]),
                        )
                        return

                    cursor.execute(
                        f"""
                        INSERT INTO {self.media_files_table}(
                            chat_id,
                            message_id,
                            media_type,
                            telegram_file_id,
                            telegram_file_unique_id,
                            original_file_name,
                            saved_file_path,
                            saved_file_size,
                            download_status,
                            created_at,
                            updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            media_type=VALUES(media_type),
                            telegram_file_id=VALUES(telegram_file_id),
                            telegram_file_unique_id=VALUES(telegram_file_unique_id),
                            original_file_name=VALUES(original_file_name),
                            saved_file_path=IFNULL(NULLIF(VALUES(saved_file_path), ''), saved_file_path),
                            saved_file_size=IF(
                                IFNULL(NULLIF(VALUES(saved_file_path), ''), '') = '',
                                saved_file_size,
                                VALUES(saved_file_size)
                            ),
                            download_status=VALUES(download_status),
                            updated_at=VALUES(updated_at)
                        """,
                        (
                            record["chat_id"],
                            record["message_id"],
                            record["media_type"],
                            record["telegram_file_id"],
                            record["telegram_file_unique_id"],
                            record["original_file_name"],
                            record["saved_file_path"],
                            record["saved_file_size"],
                            record["download_status"],
                            now,
                            now,
                        ),
                    )

                    self._upsert_message_links_mysql(cursor, record, now)

    def _upsert_message_links_sqlite(
        self,
        conn: sqlite3.Connection,
        record: Dict[str, Any],
        now: str,
    ):
        chat_id = record["chat_id"]
        media_message_id = record["message_id"]
        caption = (record.get("message_caption") or "").strip()

        conn.execute(
            f"DELETE FROM {self.message_links_table} WHERE chat_id=? AND media_message_id=?",
            (chat_id, media_message_id),
        )

        if record.get("is_separator"):
            return

        if caption:
            conn.execute(
                f"""
                INSERT INTO {self.message_links_table}(
                    chat_id,
                    media_message_id,
                    text_message_id,
                    link_type,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    media_message_id,
                    media_message_id,
                    "caption",
                    now,
                    now,
                ),
            )
            return

        row = conn.execute(
            f"""
            SELECT message_id
            FROM {self.messages_table}
            WHERE chat_id=?
              AND has_media=0
              AND message_text!=''
              AND message_id < ?
            ORDER BY message_id DESC
            LIMIT 1
            """,
            (chat_id, media_message_id),
        ).fetchone()
        if not row:
            return

        text_message_id = row[0]
        if media_message_id - text_message_id > 3:
            return

        conn.execute(
            f"""
            INSERT INTO {self.message_links_table}(
                chat_id,
                media_message_id,
                text_message_id,
                link_type,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                media_message_id,
                text_message_id,
                "nearby_text",
                now,
                now,
            ),
        )

    def _upsert_message_links_mysql(self, cursor, record: Dict[str, Any], now: str):
        chat_id = record["chat_id"]
        media_message_id = record["message_id"]
        caption = (record.get("message_caption") or "").strip()

        cursor.execute(
            f"DELETE FROM {self.message_links_table} WHERE chat_id=%s AND media_message_id=%s",
            (chat_id, media_message_id),
        )

        if record.get("is_separator"):
            return

        if caption:
            cursor.execute(
                f"""
                INSERT INTO {self.message_links_table}(
                    chat_id,
                    media_message_id,
                    text_message_id,
                    link_type,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    chat_id,
                    media_message_id,
                    media_message_id,
                    "caption",
                    now,
                    now,
                ),
            )
            return

        cursor.execute(
            f"""
            SELECT message_id
            FROM {self.messages_table}
            WHERE chat_id=%s
              AND has_media=0
              AND message_text!=''
              AND message_id < %s
            ORDER BY message_id DESC
            LIMIT 1
            """,
            (chat_id, media_message_id),
        )
        row = cursor.fetchone()
        if not row:
            return

        text_message_id = row[0]
        if media_message_id - text_message_id > 3:
            return

        cursor.execute(
            f"""
            INSERT INTO {self.message_links_table}(
                chat_id,
                media_message_id,
                text_message_id,
                link_type,
                created_at,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                chat_id,
                media_message_id,
                text_message_id,
                "nearby_text",
                now,
                now,
            ),
        )

    def close(self):
        """Reserved for compatibility."""


def build_message_record(
    chat_id: Any,
    message: Any,
    download_status: str,
    saved_file_path: Optional[str],
    is_separator: int = 0,
    separator_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a normalized record from pyrogram message."""
    media_type = message.media.value if message.media else None
    media_obj = getattr(message, media_type, None) if media_type else None

    saved_file_size = 0
    if saved_file_path and os.path.exists(saved_file_path):
        try:
            saved_file_size = os.path.getsize(saved_file_path)
        except OSError as e:
            logger.warning(f"get file size failed: {e}")

    return {
        "chat_id": str(chat_id),
        "message_id": message.id,
        "message_date": (
            message.date.strftime("%Y-%m-%d %H:%M:%S") if message.date else None
        ),
        "sender_id": message.from_user.id if message.from_user else None,
        "sender_name": message.from_user.username if message.from_user else None,
        "message_text": message.text or "",
        "message_caption": message.caption or "",
        "media_group_id": (
            str(message.media_group_id) if message.media_group_id else None
        ),
        "reply_to_message_id": message.reply_to_message_id,
        "message_thread_id": message.message_thread_id,
        "has_media": 1 if media_obj else 0,
        "media_type": media_type,
        "download_status": download_status,
        "is_separator": 1 if is_separator else 0,
        "separator_reason": separator_reason or "",
        "telegram_file_id": getattr(media_obj, "file_id", None) if media_obj else None,
        "telegram_file_unique_id": (
            getattr(media_obj, "file_unique_id", None) if media_obj else None
        ),
        "original_file_name": (
            getattr(media_obj, "file_name", None) if media_obj else None
        ),
        "saved_file_path": saved_file_path,
        "saved_file_size": saved_file_size,
    }
