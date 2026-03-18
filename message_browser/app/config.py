"""Application config for message browser."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_list(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    """Runtime settings."""

    app_host: str
    app_port: int
    app_debug: bool

    db_adapter: str
    db_table_prefix: str

    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    mysql_charset: str

    sqlite_path: str
    media_roots: List[str]

    @property
    def messages_table(self) -> str:
        return f"`{self.db_table_prefix}messages`"

    @property
    def media_files_table(self) -> str:
        return f"`{self.db_table_prefix}media_files`"

    @property
    def message_links_table(self) -> str:
        return f"`{self.db_table_prefix}message_links`"


def load_settings() -> Settings:
    """Load settings from env."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    adapter = os.getenv("DB_ADAPTER", "mysql").strip().lower()
    if adapter not in {"mysql", "sqlite"}:
        adapter = "mysql"

    table_prefix = os.getenv("DB_TABLE_PREFIX", "").strip()
    sqlite_path = os.getenv("SQLITE_PATH", "./metadata.db").strip()
    if not os.path.isabs(sqlite_path):
        sqlite_path = str((Path(__file__).resolve().parent.parent / sqlite_path).resolve())

    return Settings(
        app_host=os.getenv("APP_HOST", "0.0.0.0").strip(),
        app_port=_to_int(os.getenv("APP_PORT"), 8090),
        app_debug=_to_bool(os.getenv("APP_DEBUG"), False),
        db_adapter=adapter,
        db_table_prefix=table_prefix,
        mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1").strip(),
        mysql_port=_to_int(os.getenv("MYSQL_PORT"), 3306),
        mysql_user=os.getenv("MYSQL_USER", "root").strip(),
        mysql_password=os.getenv("MYSQL_PASSWORD", ""),
        mysql_database=os.getenv("MYSQL_DATABASE", "telegram_media_downloader").strip(),
        mysql_charset=os.getenv("MYSQL_CHARSET", "utf8mb4").strip(),
        sqlite_path=sqlite_path,
        media_roots=[str(Path(p).resolve()) for p in _to_list(os.getenv("MEDIA_ROOTS", ""))],
    )
