"""FastAPI app for browsing telegram media/text index data."""

import mimetypes
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings
from .db import Database

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


def _parse_date(date_value: Optional[str], end_of_day: bool = False) -> Optional[str]:
    if not date_value:
        return None
    try:
        _ = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式必须是 YYYY-MM-DD") from exc
    suffix = "23:59:59" if end_of_day else "00:00:00"
    return f"{date_value} {suffix}"


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
