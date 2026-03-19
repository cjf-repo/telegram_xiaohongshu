"""FastAPI app for browsing telegram media/text index data."""

import base64
import json
import math
import mimetypes
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AnyHttpUrl, BaseModel, Field

from .config import load_settings
from .db import Database
from .xhs_publisher import PlaywrightXHSPublisher, check_xhs_login_data

settings = load_settings()
db = Database(settings)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Message Browser", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
XHS_TITLE_MAX_LEN = 20


class PublishGroupRef(BaseModel):
    """Selected group reference."""

    chat_id: str = Field(..., description="chat_id")
    anchor_message_id: int = Field(..., description="group anchor message id")


class XHSPublishRequest(BaseModel):
    """Publish request."""

    groups: List[PublishGroupRef] = Field(default_factory=list)
    product_url: Optional[AnyHttpUrl] = None
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    include_separator: bool = False


class AICopyRequest(BaseModel):
    """AI copy generation request."""

    groups: List[PublishGroupRef] = Field(default_factory=list)
    include_separator: bool = False
    prompt: Optional[str] = Field(default=None, max_length=12000)
    use_vision: Optional[bool] = None
    max_images: Optional[int] = Field(default=None, ge=0, le=12)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)


def _parse_date(date_value: Optional[str], end_of_day: bool = False) -> Optional[str]:
    if not date_value:
        return None
    try:
        _ = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式必须是 YYYY-MM-DD") from exc
    suffix = "23:59:59" if end_of_day else "00:00:00"
    return f"{date_value} {suffix}"


def _normalize_xhs_title(title: str, max_len: int = XHS_TITLE_MAX_LEN) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip())
    if not text:
        text = "精选好物"
    return text[: max(1, int(max_len))]


def _caption_anchor_expr(alias: str = "m") -> str:
    """Group anchor: latest message with non-empty caption up to current row."""
    return (
        "MAX(CASE WHEN COALESCE(TRIM("
        f"{alias}.message_caption"
        "), '') <> '' THEN "
        f"{alias}.message_id END) OVER (PARTITION BY {alias}.chat_id ORDER BY "
        f"{alias}.message_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    )


def _group_cte_sql() -> str:
    """CTE for group base/header."""
    anchor_expr = _caption_anchor_expr("m")
    return f"""
    WITH group_base AS (
      SELECT
        m.chat_id,
        m.message_id,
        m.message_date,
        m.sender_id,
        m.sender_name,
        m.message_text,
        m.message_caption,
        m.media_group_id,
        m.has_media,
        m.media_type,
        m.download_status,
        m.is_separator,
        m.separator_reason,
        {anchor_expr} AS anchor_message_id
      FROM {settings.messages_table} m
    ),
    group_headers AS (
      SELECT
        b.chat_id,
        b.anchor_message_id,
        h.message_caption AS group_caption,
        h.message_date AS caption_message_date,
        MIN(b.message_id) AS first_message_id,
        MAX(b.message_id) AS latest_message_id,
        MAX(b.message_date) AS latest_message_date,
        SUM(CASE WHEN b.has_media=1 THEN 1 ELSE 0 END) AS media_count,
        SUM(CASE WHEN b.has_media=0 THEN 1 ELSE 0 END) AS text_count,
        COUNT(*) AS total_messages,
        SUM(CASE WHEN b.is_separator=1 THEN 1 ELSE 0 END) AS separator_count
      FROM group_base b
      JOIN {settings.messages_table} h
        ON h.chat_id=b.chat_id AND h.message_id=b.anchor_message_id
      WHERE b.anchor_message_id IS NOT NULL
      GROUP BY b.chat_id, b.anchor_message_id, h.message_caption, h.message_date
    )
    """


def _build_group_header_filters(
    chat_id: Optional[str],
    keyword: Optional[str],
    message_id: Optional[int],
    has_media: str,
    date_start: Optional[str],
    date_end: Optional[str],
) -> Tuple[str, List[Any]]:
    ph = db.placeholder
    conds: List[str] = []
    params: List[Any] = []

    if chat_id:
        conds.append(f"g.chat_id = {ph}")
        params.append(str(chat_id))

    media_mode = (has_media or "all").lower()
    if media_mode == "media":
        conds.append("g.media_count > 0")
    elif media_mode == "text":
        conds.append("g.text_count > 0")

    if date_start:
        conds.append(f"g.caption_message_date >= {ph}")
        params.append(date_start)

    if date_end:
        conds.append(f"g.caption_message_date <= {ph}")
        params.append(date_end)

    if message_id is not None:
        conds.append(
            f"""
            EXISTS (
              SELECT 1
              FROM group_base b3
              WHERE b3.chat_id=g.chat_id
                AND b3.anchor_message_id=g.anchor_message_id
                AND b3.message_id={ph}
            )
            """
        )
        params.append(int(message_id))

    keyword_val = (keyword or "").strip()
    if keyword_val:
        like_val = f"%{keyword_val}%"
        conds.append(
            f"""
            EXISTS (
              SELECT 1
              FROM group_base b2
              LEFT JOIN {settings.media_files_table} mf2
                ON mf2.chat_id=b2.chat_id AND mf2.message_id=b2.message_id
              WHERE b2.chat_id=g.chat_id
                AND b2.anchor_message_id=g.anchor_message_id
                AND (
                  COALESCE(b2.message_text, '') LIKE {ph}
                  OR COALESCE(b2.message_caption, '') LIKE {ph}
                  OR COALESCE(mf2.original_file_name, '') LIKE {ph}
                  OR COALESCE(mf2.saved_file_path, '') LIKE {ph}
                )
            )
            """
        )
        params.extend([like_val, like_val, like_val, like_val])

    if not conds:
        return "1=1", []
    return " AND ".join(conds), params


def _fetch_groups_by_pairs(
    anchor_pairs: List[Tuple[str, int]],
    include_separator: bool,
    summary_by_pair: Optional[Dict[Tuple[str, int], Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Load full rows for selected groups and build response."""
    if not anchor_pairs:
        return []

    or_sql, or_params = db.build_or_pairs(anchor_pairs, "b.chat_id", "b.anchor_message_id")
    cte_sql = _group_cte_sql()
    detail_sql = (
        cte_sql
        + f"""
    SELECT
      b.chat_id,
      b.anchor_message_id,
      b.message_id,
      b.message_date,
      b.sender_id,
      b.sender_name,
      b.message_text,
      b.message_caption,
      b.media_group_id,
      b.has_media,
      b.media_type,
      b.download_status,
      b.is_separator,
      b.separator_reason,
      h.group_caption,
      h.caption_message_date,
      h.first_message_id,
      h.latest_message_id,
      h.latest_message_date,
      h.media_count,
      h.text_count,
      h.total_messages,
      h.separator_count,
      mf.original_file_name,
      mf.saved_file_path,
      mf.saved_file_size,
      mf.telegram_file_id,
      mf.telegram_file_unique_id
    FROM group_base b
    JOIN group_headers h
      ON h.chat_id=b.chat_id AND h.anchor_message_id=b.anchor_message_id
    LEFT JOIN {settings.media_files_table} mf
      ON mf.chat_id=b.chat_id AND mf.message_id=b.message_id
    WHERE b.anchor_message_id IS NOT NULL AND ({or_sql})
    ORDER BY b.chat_id, b.anchor_message_id, b.message_id ASC
    """
    )
    rows = db.fetch_all(detail_sql, or_params)

    summary_by_pair = summary_by_pair or {}
    grouped: "OrderedDict[Tuple[str, int], Dict[str, Any]]" = OrderedDict()
    for row in rows:
        chat_val = db.normalize_chat_id(row.get("chat_id"))
        anchor_id = int(row.get("anchor_message_id"))
        pair = (chat_val, anchor_id)
        summary = summary_by_pair.get(pair, {})
        if pair not in grouped:
            grouped[pair] = {
                "chat_id": chat_val,
                "anchor_message_id": anchor_id,
                "group_caption": (summary.get("group_caption") or row.get("group_caption") or "").strip(),
                "caption_message_date": db.to_datetime_str(
                    summary.get("caption_message_date") or row.get("caption_message_date")
                ),
                "first_message_id": int(
                    summary.get("first_message_id")
                    or row.get("first_message_id")
                    or anchor_id
                ),
                "latest_message_id": int(
                    summary.get("latest_message_id")
                    or row.get("latest_message_id")
                    or 0
                ),
                "latest_message_date": db.to_datetime_str(
                    summary.get("latest_message_date") or row.get("latest_message_date")
                ),
                "primary_text": (summary.get("group_caption") or row.get("group_caption") or "").strip(),
                "text_messages": [],
                "media_items": [],
                "message_ids": [],
                "total_messages": int(
                    summary.get("total_messages")
                    or row.get("total_messages")
                    or 0
                ),
                "separator_count": int(
                    summary.get("separator_count")
                    or row.get("separator_count")
                    or 0
                ),
            }

        group = grouped[pair]
        msg_id = int(row.get("message_id") or 0)
        group["message_ids"].append(msg_id)
        msg_date = db.to_datetime_str(row.get("message_date"))
        has_media_flag = int(row.get("has_media") or 0) == 1
        is_separator_flag = int(row.get("is_separator") or 0) == 1

        text_value = (row.get("message_text") or "").strip()
        caption_value = (row.get("message_caption") or "").strip()
        merged_text = text_value or caption_value
        if merged_text:
            group["text_messages"].append(
                {
                    "message_id": msg_id,
                    "message_date": msg_date,
                    "text": merged_text,
                }
            )
            if not group["primary_text"]:
                group["primary_text"] = merged_text

        if has_media_flag:
            if (not include_separator) and is_separator_flag:
                continue
            group["media_items"].append(
                {
                    "message_id": msg_id,
                    "message_date": msg_date,
                    "media_type": row.get("media_type"),
                    "media_group_id": row.get("media_group_id"),
                    "download_status": row.get("download_status"),
                    "original_file_name": row.get("original_file_name"),
                    "saved_file_path": row.get("saved_file_path"),
                    "saved_file_size": int(row.get("saved_file_size") or 0),
                    "telegram_file_id": row.get("telegram_file_id"),
                    "telegram_file_unique_id": row.get("telegram_file_unique_id"),
                    "is_separator": 1 if is_separator_flag else 0,
                    "separator_reason": row.get("separator_reason"),
                }
            )

    items: List[Dict[str, Any]] = []
    for pair in anchor_pairs:
        group = grouped.get(pair)
        if group:
            items.append(group)
    return items


def _dedupe_keep_order(values: List[Any]) -> List[Any]:
    result: List[Any] = []
    seen = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _build_xhs_payload(groups: List[Dict[str, Any]], req: XHSPublishRequest) -> Dict[str, Any]:
    """Build merged payload from selected groups."""
    captions: List[str] = []
    text_blocks: List[str] = []
    media_assets: List[Dict[str, Any]] = []
    all_message_ids: List[int] = []

    for group in groups:
        caption = (group.get("group_caption") or group.get("primary_text") or "").strip()
        if caption:
            captions.append(caption)

        if caption:
            text_blocks.append(caption)
        for txt in group.get("text_messages", []):
            val = (txt.get("text") or "").strip()
            if val and val != caption:
                text_blocks.append(val)

        for media in group.get("media_items", []):
            saved_path = (media.get("saved_file_path") or "").strip()
            if not saved_path:
                continue
            media_assets.append(
                {
                    "chat_id": str(group.get("chat_id")),
                    "anchor_message_id": int(group.get("anchor_message_id")),
                    "message_id": int(media.get("message_id") or 0),
                    "media_type": media.get("media_type"),
                    "saved_file_path": saved_path,
                    "original_file_name": media.get("original_file_name"),
                }
            )

        for mid in group.get("message_ids", []):
            try:
                all_message_ids.append(int(mid))
            except (TypeError, ValueError):
                continue

    media_assets = _dedupe_keep_order(media_assets)
    text_blocks = _dedupe_keep_order([it for it in text_blocks if it])
    message_ids = sorted(set(all_message_ids))

    auto_title = ""
    if captions:
        auto_title = captions[0]
    elif text_blocks:
        auto_title = text_blocks[0]

    title = _normalize_xhs_title((req.title or "").strip() or auto_title or "自动上架商品")
    description = (req.description or "").strip() or "\n\n".join(text_blocks)[:5000]

    return {
        "source": "message_browser",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "product_url": str(req.product_url) if req.product_url else "",
        "title": title,
        "description": description,
        "group_count": len(groups),
        "message_id_count": len(message_ids),
        "message_ids": message_ids,
        "media_count": len(media_assets),
        "media_assets": media_assets,
        "groups": [
            {
                "chat_id": str(group.get("chat_id")),
                "anchor_message_id": int(group.get("anchor_message_id")),
                "first_message_id": int(group.get("first_message_id") or 0),
                "latest_message_id": int(group.get("latest_message_id") or 0),
                "group_caption": group.get("group_caption") or "",
                "media_count": len(group.get("media_items", [])),
            }
            for group in groups
        ],
    }


def _image_file_to_data_url(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "image/jpeg"
    image_bytes = image_path.read_bytes()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _extract_message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text_val = item.get("text") or item.get("content")
                if text_val:
                    chunks.append(str(text_val))
            elif item:
                chunks.append(str(item))
        merged = "\n".join(chunks).strip()
        if merged:
            return merged
    reasoning_content = getattr(message, "reasoning_content", None)
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return reasoning_content.strip()
    return ""


def _extract_json_dict(raw_text: str) -> Optional[Dict[str, Any]]:
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


_COST_DISCLOSURE_PATTERN = re.compile(
    r"(成本|进价|批价|拿货价|利润|毛利|净利|赚\d*元|加价)",
    flags=re.IGNORECASE,
)
_PRICE_TOKEN_PATTERN = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[rR元块¥])")
_COST_PRICE_PATTERNS = [
    re.compile(
        r"(?:批价|进价|拿货价|成本价|成本)\s*[：:]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|¥|R|r)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|¥)\s*(?:批价|进价|拿货价|成本价|成本)",
        re.IGNORECASE,
    ),
]


def _remove_cost_disclosure_text(text: str) -> str:
    if not text:
        return text
    source = str(text).strip()
    if not source:
        return source

    parts = re.split(r"(?<=[。！？!?；;\n])", source)
    kept: List[str] = []
    for part in parts:
        seg = part.strip()
        if not seg:
            continue
        if _COST_DISCLOSURE_PATTERN.search(seg):
            continue
        kept.append(seg)

    merged = "\n".join(kept).strip()
    if len(merged) >= 20:
        return merged

    # 兜底：如果清理后内容过短，则仅替换敏感词，保留主体文案
    softened = _COST_DISCLOSURE_PATTERN.sub("包邮售价", source)
    softened = re.sub(r"\n{3,}", "\n\n", softened).strip()
    return softened


def _to_positive_float(value: Any) -> Optional[float]:
    try:
        num = float(str(value).strip())
    except Exception:
        return None
    if not math.isfinite(num):
        return None
    if num <= 0:
        return None
    return num


def _extract_cost_price_from_text(text: str) -> Optional[float]:
    source = (text or "").strip()
    if not source:
        return None
    for pattern in _COST_PRICE_PATTERNS:
        match = pattern.search(source)
        if not match:
            continue
        num = _to_positive_float(match.group(1))
        if num is None:
            continue
        if 1 <= num <= 100000:
            return num
    return None


def _psychological_public_price(raw_price: float, floor_price: float) -> int:
    floor_int = int(math.ceil(max(floor_price, 1)))
    price = max(int(round(raw_price)), floor_int)
    if price % 100 == 0:
        candidate = price - 1
        if candidate >= floor_int:
            price = candidate
        else:
            price = price + 9
    return int(price)


def _build_public_price_plan(cost_price: Optional[float]) -> Dict[str, Any]:
    if cost_price is None:
        return {}
    # 薄利走量：包邮场景下快递约15元 + 净利5~10元
    event_floor = cost_price + 20
    recommended_floor = cost_price + 25
    event_price = _psychological_public_price(event_floor, event_floor)
    recommended_price = _psychological_public_price(recommended_floor, recommended_floor)
    if recommended_price < event_price:
        recommended_price = event_price
    return {
        "cost_price_detected": round(float(cost_price), 2),
        "event_floor": int(math.ceil(event_floor)),
        "recommended_floor": int(math.ceil(recommended_floor)),
        "event_price": int(event_price),
        "recommended_price": int(recommended_price),
    }


def _replace_low_public_price(
    text: str,
    *,
    min_public_price: int,
    replacement_price: int,
) -> str:
    source = (text or "").strip()
    if not source or min_public_price <= 0 or replacement_price <= 0:
        return source

    def _repl(match: re.Match) -> str:
        num = _to_positive_float(match.group("num"))
        if num is None:
            return match.group(0)
        if num + 1e-6 >= float(min_public_price):
            return match.group(0)
        unit = match.group("unit")
        if unit in {"r", "R"}:
            return f"{int(replacement_price)}{unit}"
        if unit in {"元", "块"}:
            return f"{int(replacement_price)}元"
        return f"{int(replacement_price)}¥"

    return _PRICE_TOKEN_PATTERN.sub(_repl, source)


def _response_choices_count(response: Any) -> int:
    choices = getattr(response, "choices", None)
    if choices is None:
        return 0
    try:
        return len(choices)
    except Exception:
        return 0


def _generate_ai_copy_result(payload: Dict[str, Any], req: AICopyRequest) -> Dict[str, Any]:
    if not settings.ai_enabled:
        raise HTTPException(status_code=400, detail="AI 功能已关闭（AI_ENABLED=false）")
    if not settings.ai_api_key:
        raise HTTPException(status_code=400, detail="未配置 AI_API_KEY")

    try:
        from openai import OpenAI
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"openai SDK 不可用，请先安装依赖: {exc}",
        ) from exc

    source_title = (payload.get("title") or "").strip()
    source_text = (payload.get("description") or "").strip()
    detected_cost_price = _extract_cost_price_from_text(source_text) or _extract_cost_price_from_text(
        source_title
    )
    public_price_plan = _build_public_price_plan(detected_cost_price)
    media_assets = payload.get("media_assets") or []
    prompt_text = (req.prompt or settings.ai_default_prompt or "").strip()
    if not prompt_text:
        prompt_text = (
            "请基于给定信息生成小红书爆款文案，并输出JSON（title, content, highlights, hashtags）。"
        )

    max_images = settings.ai_max_images if req.max_images is None else int(req.max_images)
    max_images = max(0, min(max_images, 12))
    use_vision = settings.ai_default_use_vision if req.use_vision is None else bool(req.use_vision)
    temperature = settings.ai_temperature if req.temperature is None else float(req.temperature)
    temperature = max(0.0, min(temperature, 2.0))

    image_content_items: List[Dict[str, Any]] = []
    used_image_paths: List[str] = []
    if use_vision and max_images > 0:
        for media in media_assets:
            if len(image_content_items) >= max_images:
                break
            media_type = (media.get("media_type") or "").lower()
            if media_type not in {"photo", "image"}:
                continue
            raw_path = (media.get("saved_file_path") or "").strip()
            if not raw_path:
                continue
            path_obj = Path(raw_path)
            if not path_obj.is_absolute():
                path_obj = (Path.cwd() / path_obj).resolve()
            else:
                path_obj = path_obj.resolve()
            if (not path_obj.exists()) or (not path_obj.is_file()):
                continue
            if not _check_path_allowed(path_obj):
                continue
            try:
                data_url = _image_file_to_data_url(path_obj)
            except Exception:
                continue
            image_content_items.append(
                {"type": "image_url", "image_url": {"url": data_url}}
            )
            used_image_paths.append(str(path_obj))

    source_block = (
        "【输入标题候选】\n"
        f"{source_title or '-'}\n\n"
        "【输入文本】\n"
        f"{source_text or '-'}\n\n"
        f"【分组数】{payload.get('group_count') or 0}\n"
        f"【消息数】{payload.get('message_id_count') or 0}\n"
        f"【媒体数】{payload.get('media_count') or 0}\n"
    )
    user_text = (
        f"{prompt_text}\n\n"
        "请严格输出 JSON 对象，不要使用 markdown 代码块。\n"
        "JSON字段建议: title, content, highlights, hashtags。\n\n"
        f"{source_block}"
    )
    if used_image_paths:
        user_text += f"\n【附加图片】共 {len(used_image_paths)} 张，请综合图片信息生成文案。"

    model_name = settings.ai_text_model
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": settings.ai_system_prompt},
    ]
    if used_image_paths:
        model_name = settings.ai_vision_model or settings.ai_text_model
        content_items = [{"type": "text", "text": user_text}, *image_content_items]
        messages.append({"role": "user", "content": content_items})
    else:
        messages.append({"role": "user", "content": user_text})

    client = OpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        timeout=settings.ai_timeout,
    )
    attempt_logs: List[str] = []
    final_model_name = model_name
    final_used_vision = bool(used_image_paths)
    response = None
    raw_text = ""

    def _call_completion(
        call_model: str,
        call_messages: List[Dict[str, Any]],
        *,
        use_extra_body: bool,
    ):
        kwargs: Dict[str, Any] = {
            "model": call_model,
            "messages": call_messages,
            "temperature": temperature,
        }
        if use_extra_body:
            kwargs["extra_body"] = {"enable_thinking": False}
        return client.chat.completions.create(**kwargs)

    def _extract_content_from_response(resp: Any) -> str:
        if not resp or _response_choices_count(resp) <= 0:
            return ""
        try:
            msg = resp.choices[0].message
        except Exception:
            return ""
        return _extract_message_content(msg)

    # A1: 主请求（视觉模型时默认不带 extra_body，兼容更多网关）
    try:
        response = _call_completion(
            model_name,
            messages,
            use_extra_body=not bool(used_image_paths),
        )
        raw_text = _extract_content_from_response(response)
        attempt_logs.append(
            f"A1 model={model_name} choices={_response_choices_count(response)} content={bool(raw_text)}"
        )
    except Exception as exc:
        attempt_logs.append(f"A1 error={exc}")

    # A2: 视觉场景降级重试（去掉system，减少结构约束）
    if (not raw_text) and used_image_paths:
        try:
            vision_model = settings.ai_vision_model or settings.ai_text_model
            retry_messages = [messages[-1]] if messages else []
            response = _call_completion(
                vision_model,
                retry_messages,
                use_extra_body=False,
            )
            raw_text = _extract_content_from_response(response)
            final_model_name = vision_model
            final_used_vision = True
            attempt_logs.append(
                f"A2 model={vision_model} choices={_response_choices_count(response)} content={bool(raw_text)}"
            )
        except Exception as exc:
            attempt_logs.append(f"A2 error={exc}")

    # A3: 最终兜底，降级到文本模型（不丢任务）
    if not raw_text:
        try:
            text_model = settings.ai_text_model
            text_messages: List[Dict[str, Any]] = [
                {"role": "system", "content": settings.ai_system_prompt},
                {"role": "user", "content": user_text},
            ]
            response = _call_completion(
                text_model,
                text_messages,
                use_extra_body=True,
            )
            raw_text = _extract_content_from_response(response)
            final_model_name = text_model
            final_used_vision = False
            attempt_logs.append(
                f"A3 model={text_model} choices={_response_choices_count(response)} content={bool(raw_text)}"
            )
        except Exception as exc:
            attempt_logs.append(f"A3 error={exc}")

    if not raw_text:
        attempt_text = " | ".join(attempt_logs)
        raise HTTPException(
            status_code=502,
            detail=f"AI 返回为空（无可用content）。{attempt_text[:800]}",
        )

    parsed = _extract_json_dict(raw_text) or {}
    title = str(parsed.get("title") or source_title or "").strip()
    content = str(parsed.get("content") or raw_text).strip()
    highlights = parsed.get("highlights")
    hashtags = parsed.get("hashtags")
    strategy = parsed.get("strategy")
    pricing = parsed.get("pricing")
    titles = parsed.get("titles")
    style_variants = parsed.get("style_variants")
    if not isinstance(highlights, list):
        highlights = []
    if not isinstance(hashtags, list):
        hashtags = []
    if not isinstance(strategy, dict):
        strategy = {}
    if not isinstance(pricing, dict):
        pricing = {}
    if not isinstance(titles, list):
        titles = []
    if not isinstance(style_variants, list):
        style_variants = []

    min_public_price = 0
    replacement_public_price = 0
    if public_price_plan:
        rec_input = _to_positive_float(pricing.get("recommended_price"))
        evt_input = _to_positive_float(pricing.get("event_price"))

        rec_floor = float(public_price_plan["recommended_floor"])
        evt_floor = float(public_price_plan["event_floor"])
        rec_final = _psychological_public_price(
            max(rec_floor, rec_input or 0.0),
            rec_floor,
        )
        evt_final = _psychological_public_price(
            max(evt_floor, evt_input or 0.0),
            evt_floor,
        )
        if rec_final < evt_final:
            rec_final = evt_final

        pricing["cost_price_detected"] = public_price_plan["cost_price_detected"]
        pricing["recommended_price"] = rec_final
        pricing["event_price"] = evt_final
        pricing["pricing_note"] = (
            "对外仅展示包邮卖价与活动价，已自动隐藏进货与利润信息。"
            "活动价用于引流，建议售价用于日常成交。"
        )
        min_public_price = int(public_price_plan["event_floor"])
        replacement_public_price = int(evt_final)
    else:
        pricing_note = str(pricing.get("pricing_note") or "")
        pricing["pricing_note"] = _remove_cost_disclosure_text(pricing_note)

    def _sanitize_public_copy_text(text: str) -> str:
        cleaned = _replace_low_public_price(
            text,
            min_public_price=min_public_price,
            replacement_price=replacement_public_price,
        )
        cleaned = _remove_cost_disclosure_text(cleaned)
        return cleaned

    # 强制去除成本/利润披露，并约束标题长度（避免发布失败）
    title = _normalize_xhs_title(_sanitize_public_copy_text(title))
    content = _sanitize_public_copy_text(content)
    safe_titles = [
        _normalize_xhs_title(_sanitize_public_copy_text(str(it)))
        for it in titles
        if it is not None
    ]
    safe_style_variants: List[Dict[str, Any]] = []
    for item in style_variants:
        if not isinstance(item, dict):
            continue
        safe_style_variants.append(
            {
                "style": _sanitize_public_copy_text(str(item.get("style") or "")).strip(),
                "title": _normalize_xhs_title(
                    _sanitize_public_copy_text(str(item.get("title") or "")).strip()
                ),
                "content": _sanitize_public_copy_text(str(item.get("content") or "")).strip(),
            }
        )
    pricing_note = _sanitize_public_copy_text(str(pricing.get("pricing_note") or "")).strip()
    pricing["pricing_note"] = pricing_note

    return {
        "model": final_model_name,
        "used_vision": final_used_vision,
        "used_image_count": len(used_image_paths),
        "used_image_paths": used_image_paths,
        "title": title,
        "content": content,
        "highlights": [str(it) for it in highlights if it is not None],
        "hashtags": [str(it) for it in hashtags if it is not None],
        "strategy": strategy,
        "pricing": pricing,
        "titles": safe_titles,
        "style_variants": safe_style_variants,
        "raw_text": raw_text,
        "attempt_logs": attempt_logs,
    }


def _send_xhs_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send payload by mode."""
    mode = (settings.xhs_publish_mode or "mock").lower()
    if mode == "mock":
        out_dir = Path(settings.xhs_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"xhs_publish_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out_file = out_dir / file_name
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "mode": "mock",
            "saved_file": str(out_file),
        }

    if mode == "webhook":
        webhook_url = settings.xhs_webhook_url.strip()
        if not webhook_url:
            raise HTTPException(
                status_code=400,
                detail="XHS_WEBHOOK_URL 未配置，无法使用 webhook 模式",
            )

        headers = {"Content-Type": "application/json; charset=utf-8"}
        if settings.xhs_webhook_token:
            headers["Authorization"] = f"Bearer {settings.xhs_webhook_token}"

        request = Request(
            webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(settings.xhs_timeout, 1)) as response:
                body = response.read().decode("utf-8", errors="ignore")
                return {
                    "mode": "webhook",
                    "status_code": response.status,
                    "response": body[:2000],
                }
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise HTTPException(
                status_code=502,
                detail=f"Webhook HTTPError {exc.code}: {body[:500]}",
            ) from exc
        except URLError as exc:
            raise HTTPException(status_code=502, detail=f"Webhook 请求失败: {exc}") from exc

    if mode == "playwright":
        publisher = PlaywrightXHSPublisher(
            creator_url=settings.xhs_creator_url,
            user_data_dir=settings.xhs_user_data_dir,
            auto_click_publish=settings.xhs_auto_click_publish,
            publish_button_text=settings.xhs_publish_button_text,
            wait_timeout_ms=settings.xhs_wait_timeout_ms,
            proxy_server=settings.xhs_proxy_server,
            proxy_username=settings.xhs_proxy_username,
            proxy_password=settings.xhs_proxy_password,
        )
        publish_body = payload.get("description") or ""
        product_url = payload.get("product_url") or ""
        if product_url and str(product_url) not in publish_body:
            publish_body = f"{publish_body}\n\n商品链接：{product_url}".strip()

        result = publisher.publish(
            title=str(payload.get("title") or "自动上架商品"),
            body=publish_body,
            media_paths=[it.get("saved_file_path") for it in payload.get("media_assets", [])],
        )
        if not result.success:
            raise HTTPException(status_code=400, detail=result.error or "发布失败")
        return {
            "mode": "playwright",
            "note_id": result.note_id,
            "note_url": result.note_url,
        }

    raise HTTPException(status_code=400, detail=f"不支持的 XHS_PUBLISH_MODE: {mode}")


@app.get("/")
def index():
    """Serve SPA page."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health():
    """Health endpoint."""
    return {"ok": True, "adapter": settings.db_adapter}


@app.get("/api/chats")
def list_chats():
    """Get all chats summary."""
    sql = f"""
    SELECT
      m.chat_id,
      COUNT(*) AS total_messages,
      SUM(CASE WHEN m.has_media=1 THEN 1 ELSE 0 END) AS total_media,
      MAX(m.message_date) AS latest_message_date
    FROM {settings.messages_table} m
    GROUP BY m.chat_id
    ORDER BY latest_message_date DESC
    """
    rows = db.fetch_all(sql)
    for row in rows:
        row["chat_id"] = db.normalize_chat_id(row.get("chat_id"))
        row["latest_message_date"] = db.to_datetime_str(row.get("latest_message_date"))
        row["total_messages"] = int(row.get("total_messages") or 0)
        row["total_media"] = int(row.get("total_media") or 0)
    return {"items": rows}


@app.get("/api/groups")
def list_groups(
    chat_id: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    message_id: Optional[int] = Query(default=None),
    has_media: str = Query(default="all"),
    include_separator: bool = Query(default=False),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List grouped data by caption-block rule."""
    start_time = _parse_date(date_from, end_of_day=False)
    end_time = _parse_date(date_to, end_of_day=True)
    where_sql, where_params = _build_group_header_filters(
        chat_id=chat_id,
        keyword=keyword,
        message_id=message_id,
        has_media=has_media,
        date_start=start_time,
        date_end=end_time,
    )

    cte_sql = _group_cte_sql()
    count_sql = cte_sql + f"SELECT COUNT(1) AS total FROM group_headers g WHERE {where_sql}"
    total_row = db.fetch_one(count_sql, where_params) or {"total": 0}
    total = int(total_row.get("total") or 0)

    offset = (page - 1) * page_size
    ph = db.placeholder
    page_sql = (
        cte_sql
        + f"""
    SELECT
      g.chat_id,
      g.anchor_message_id,
      g.group_caption,
      g.caption_message_date,
      g.first_message_id,
      g.latest_message_id,
      g.latest_message_date,
      g.media_count,
      g.text_count,
      g.total_messages,
      g.separator_count
    FROM group_headers g
    WHERE {where_sql}
    ORDER BY g.latest_message_id DESC
    LIMIT {ph} OFFSET {ph}
    """
    )
    page_rows = db.fetch_all(page_sql, [*where_params, page_size, offset])

    anchor_pairs: List[Tuple[str, int]] = []
    summary_by_pair: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in page_rows:
        pair = (db.normalize_chat_id(row.get("chat_id")), int(row.get("anchor_message_id")))
        anchor_pairs.append(pair)
        summary_by_pair[pair] = {
            "group_caption": row.get("group_caption"),
            "caption_message_date": row.get("caption_message_date"),
            "first_message_id": row.get("first_message_id"),
            "latest_message_id": row.get("latest_message_id"),
            "latest_message_date": row.get("latest_message_date"),
            "total_messages": row.get("total_messages"),
            "separator_count": row.get("separator_count"),
        }

    if not anchor_pairs:
        return {
            "items": [],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }

    items = _fetch_groups_by_pairs(
        anchor_pairs=anchor_pairs,
        include_separator=include_separator,
        summary_by_pair=summary_by_pair,
    )

    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


@app.get("/api/groups/{chat_id}/{anchor_message_id}")
def get_group(chat_id: str, anchor_message_id: int):
    """Get one group by chat_id and anchor_message_id."""
    pair = (str(chat_id), int(anchor_message_id))
    items = _fetch_groups_by_pairs(
        anchor_pairs=[pair],
        include_separator=True,
        summary_by_pair=None,
    )
    if items:
        return items[0]
    raise HTTPException(status_code=404, detail="分组不存在")


@app.post("/api/xhs/publish/preview")
def preview_xhs_publish(req: XHSPublishRequest):
    """Preview merged payload for xhs publish."""
    if not req.groups:
        raise HTTPException(status_code=400, detail="请至少选择一个分组")

    anchor_pairs = _dedupe_keep_order(
        [(str(group.chat_id), int(group.anchor_message_id)) for group in req.groups]
    )
    groups = _fetch_groups_by_pairs(
        anchor_pairs=anchor_pairs,
        include_separator=req.include_separator,
        summary_by_pair=None,
    )
    if not groups:
        raise HTTPException(status_code=404, detail="未找到选中的分组数据")

    payload = _build_xhs_payload(groups, req)
    return {
        "ok": True,
        "mode": settings.xhs_publish_mode,
        "payload": payload,
    }


@app.post("/api/xhs/publish")
def publish_to_xhs(req: XHSPublishRequest):
    """Publish merged data to xhs adapter."""
    if not req.groups:
        raise HTTPException(status_code=400, detail="请至少选择一个分组")

    anchor_pairs = _dedupe_keep_order(
        [(str(group.chat_id), int(group.anchor_message_id)) for group in req.groups]
    )
    groups = _fetch_groups_by_pairs(
        anchor_pairs=anchor_pairs,
        include_separator=req.include_separator,
        summary_by_pair=None,
    )
    if not groups:
        raise HTTPException(status_code=404, detail="未找到选中的分组数据")

    payload = _build_xhs_payload(groups, req)
    publish_result = _send_xhs_payload(payload)
    return {
        "ok": True,
        "publish_result": publish_result,
        "summary": {
            "group_count": payload["group_count"],
            "message_id_count": payload["message_id_count"],
            "media_count": payload["media_count"],
            "title": payload["title"],
        },
    }


@app.post("/api/ai/copy/generate")
def generate_ai_copy(req: AICopyRequest):
    """Generate AI copy by selected groups."""
    if not req.groups:
        raise HTTPException(status_code=400, detail="请至少选择一个分组")

    anchor_pairs = _dedupe_keep_order(
        [(str(group.chat_id), int(group.anchor_message_id)) for group in req.groups]
    )
    groups = _fetch_groups_by_pairs(
        anchor_pairs=anchor_pairs,
        include_separator=req.include_separator,
        summary_by_pair=None,
    )
    if not groups:
        raise HTTPException(status_code=404, detail="未找到选中的分组数据")

    source_req = XHSPublishRequest(
        groups=[],
        product_url=None,
        title=None,
        description=None,
        include_separator=req.include_separator,
    )
    source_payload = _build_xhs_payload(groups, source_req)
    ai_result = _generate_ai_copy_result(source_payload, req)
    return {
        "ok": True,
        "source_summary": {
            "group_count": source_payload.get("group_count") or 0,
            "message_id_count": source_payload.get("message_id_count") or 0,
            "media_count": source_payload.get("media_count") or 0,
        },
        "result": ai_result,
    }


@app.get("/api/xhs/status")
def xhs_status():
    """Check xhs integration status."""
    mode = (settings.xhs_publish_mode or "mock").lower()
    status = {
        "mode": mode,
        "creator_url": settings.xhs_creator_url,
        "user_data_dir": settings.xhs_user_data_dir,
        "auto_click_publish": settings.xhs_auto_click_publish,
        "proxy_enabled": bool(settings.xhs_proxy_server),
        "ok": True,
        "message": "mock/webhook 模式无需本地登录态",
    }
    if mode == "playwright":
        login_status = check_xhs_login_data(settings.xhs_user_data_dir)
        status["ok"] = bool(login_status.get("ok"))
        status["message"] = login_status.get("message")
        status["login_command"] = "python scripts/xhs_login.py"
    return status


@app.get("/api/ai/status")
def ai_status():
    """Check AI copywriter integration status."""
    has_key = bool(settings.ai_api_key)
    enabled = bool(settings.ai_enabled)
    return {
        "enabled": enabled,
        "ready": bool(enabled and has_key),
        "message": "就绪" if (enabled and has_key) else "请检查 AI_ENABLED / AI_API_KEY",
        "base_url": settings.ai_base_url,
        "text_model": settings.ai_text_model,
        "vision_model": settings.ai_vision_model,
        "default_use_vision": settings.ai_default_use_vision,
        "max_images": settings.ai_max_images,
    }


def _check_path_allowed(target: Path) -> bool:
    if not target.exists() or not target.is_file():
        return False
    if not settings.media_roots:
        return True
    for root_str in settings.media_roots:
        root = Path(root_str)
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@app.get("/api/media")
def media_file(path: str = Query(..., description="saved_file_path")):
    """Serve local media files for preview."""
    decoded = unquote(path)
    candidate = Path(decoded)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.resolve()

    if not _check_path_allowed(candidate):
        raise HTTPException(status_code=404, detail="文件不存在或不在允许目录内")

    media_type, _ = mimetypes.guess_type(str(candidate))
    return FileResponse(str(candidate), media_type=media_type or "application/octet-stream")
