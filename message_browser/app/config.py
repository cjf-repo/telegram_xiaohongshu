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


def _to_float(value: str, default: float) -> float:
    try:
        return float(value)
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

    ai_enabled: bool
    ai_api_key: str
    ai_base_url: str
    ai_text_model: str
    ai_vision_model: str
    ai_default_use_vision: bool
    ai_max_images: int
    ai_temperature: float
    ai_timeout: int
    ai_system_prompt: str
    ai_default_prompt: str

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
        ai_enabled=_to_bool(os.getenv("AI_ENABLED"), True),
        ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        ai_base_url=os.getenv("AI_BASE_URL", "https://api-inference.modelscope.cn/v1").strip(),
        ai_text_model=os.getenv("AI_TEXT_MODEL", "Qwen/Qwen3-1.7B").strip(),
        ai_vision_model=os.getenv("AI_VISION_MODEL", "moonshotai/Kimi-K2.5").strip(),
        ai_default_use_vision=_to_bool(os.getenv("AI_DEFAULT_USE_VISION"), True),
        ai_max_images=max(0, _to_int(os.getenv("AI_MAX_IMAGES"), 4)),
        ai_temperature=max(0.0, min(2.0, _to_float(os.getenv("AI_TEMPERATURE"), 0.7))),
        ai_timeout=max(5, _to_int(os.getenv("AI_TIMEOUT"), 120)),
        ai_system_prompt=os.getenv(
            "AI_SYSTEM_PROMPT",
            (
                "你是资深小红书电商文案策划，擅长基于商品图文自动完成受众分析、"
                "风格决策与高转化文案生成。"
            ),
        ).strip(),
        ai_default_prompt=os.getenv(
            "AI_DEFAULT_PROMPT",
            (
                "你将收到商品文本与图片信息，请直接生成“小红书种草文案包”。\n"
                "不要反问用户，不要分步骤等待确认，自动完成决策。\n\n"
                "【自动决策要求】\n"
                "1. 自动识别赛道、人群、痛点、购买动机。\n"
                "2. 在5种风格中自动选择主风格并给出2个备选：幽默、科普、情感共鸣、实用、分享。\n"
                "3. 在5种框架中自动选择最优1个：真实体验分享、专业测评、故事情境带入、数据支撑、使用教程。\n"
                "4. 生成5个可选标题（20字以内，带emoji，情绪钩子强）。\n"
                "5. 生成1篇主文案（约500字，口语化、接地气、分段清晰、有行动引导）。\n"
                "6. 再生成2篇不同风格短文案（每篇120-180字）用于A/B测试。\n\n"
                "【价格策略（重点）】\n"
                "1. 若输入中出现“批价/进价/拿货价/成本价”，将其视为进货成本价，不是对外售价。\n"
                "2. 默认按包邮测算：快递成本约15元，目标净利润仅5-10元（薄利走量）。\n"
                "3. 因此包邮建议售价区间=成本+20 到 成本+25；若明确不包邮，则建议售价区间=成本+5 到 成本+10。\n"
                "4. 若建议价刚好是整百（100/200/300），优先改为99/199/299心理价；但必须保证净利润仍>=5元，"
                "若不足则改为满足利润的x9价格（如109/209）。\n"
                "5. 价格表达要自然，适合小红书交易场景，给“建议到手价”与“活动价”两档，整体偏亲民以提高成交。\n\n"
                "6. 严禁在最终标题和文案正文里出现成本价、进货价、批价、利润等内部信息；"
                "对外只表达包邮卖价/活动价。\n\n"
                "7. 若标题中出现价格数字，该价格必须使用对外活动价或建议售价，"
                "不能出现接近成本价的数字。\n\n"
                "【合规与真实性】\n"
                "- 忠于输入信息，不编造未提供的参数、功效或资质。\n"
                "- 避免绝对化、医疗化、极限词；必要时用拼音或emoji柔化敏感表达。\n"
                "- 可以适度夸张语气，但不得脱离商品实际。\n\n"
                "【输出格式】\n"
                "必须只输出JSON对象，不要markdown，不要额外解释。\n"
                "字段要求：\n"
                "{\n"
                '  "title": "主标题（从titles中选最优）",\n'
                '  "content": "主文案（约500字）",\n'
                '  "highlights": ["核心卖点1","核心卖点2","核心卖点3"],\n'
                '  "hashtags": ["#标签1","#标签2","#标签3","#标签4","#标签5"],\n'
                '  "strategy": {\n'
                '    "track": "识别出的赛道",\n'
                '    "audience": "目标人群",\n'
                '    "main_style": "主风格",\n'
                '    "backup_styles": ["备选风格1","备选风格2"],\n'
                '    "framework": "采用框架",\n'
                '    "reason": "选择理由"\n'
                "  },\n"
                '  "pricing": {\n'
                '    "cost_price_detected": 0,\n'
                '    "recommended_price": 0,\n'
                '    "event_price": 0,\n'
                '    "pricing_note": "定价逻辑说明"\n'
                "  },\n"
                '  "titles": ["标题1","标题2","标题3","标题4","标题5"],\n'
                '  "style_variants": [\n'
                '    {"style":"风格A","title":"标题A","content":"短文案A"},\n'
                '    {"style":"风格B","title":"标题B","content":"短文案B"}\n'
                "  ]\n"
                "}"
            ),
        ).strip(),
    )
