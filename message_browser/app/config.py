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

    xhs_publish_mode: str
    xhs_webhook_url: str
    xhs_webhook_token: str
    xhs_timeout: int
    xhs_output_dir: str
    xhs_creator_url: str
    xhs_user_data_dir: str
    xhs_auto_click_publish: bool
    xhs_publish_button_text: str
    xhs_wait_timeout_ms: int
    xhs_proxy_server: str
    xhs_proxy_username: str
    xhs_proxy_password: str

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
        xhs_publish_mode=os.getenv("XHS_PUBLISH_MODE", "mock").strip().lower(),
        xhs_webhook_url=os.getenv("XHS_WEBHOOK_URL", "").strip(),
        xhs_webhook_token=os.getenv("XHS_WEBHOOK_TOKEN", "").strip(),
        xhs_timeout=_to_int(os.getenv("XHS_TIMEOUT"), 20),
        xhs_output_dir=str(
            (
                Path(__file__).resolve().parent.parent
                / os.getenv("XHS_OUTPUT_DIR", "./output/xhs_publish")
            ).resolve()
        ),
        xhs_creator_url=os.getenv(
            "XHS_CREATOR_URL", "https://creator.xiaohongshu.com/publish/publish"
        ).strip(),
        xhs_user_data_dir=str(
            (
                Path(__file__).resolve().parent.parent
                / os.getenv("XHS_USER_DATA_DIR", "./browser_data")
            ).resolve()
        ),
        xhs_auto_click_publish=_to_bool(os.getenv("XHS_AUTO_CLICK_PUBLISH"), False),
        xhs_publish_button_text=os.getenv("XHS_PUBLISH_BUTTON_TEXT", "发布").strip(),
        xhs_wait_timeout_ms=_to_int(os.getenv("XHS_WAIT_TIMEOUT_MS"), 90000),
        xhs_proxy_server=os.getenv("XHS_PROXY_SERVER", "").strip(),
        xhs_proxy_username=os.getenv("XHS_PROXY_USERNAME", "").strip(),
        xhs_proxy_password=os.getenv("XHS_PROXY_PASSWORD", "").strip(),
    )
