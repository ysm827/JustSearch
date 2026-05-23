"""
History router – /api/history endpoints
"""

import logging

from fastapi import APIRouter, HTTPException, Body, Query
from sqlalchemy import text as sql_text

from ..database import (
    list_chats, load_chat_history, save_chat_history,
    delete_chat, get_chat_path, delete_all_chats, get_session,
    list_chat_groups, create_chat_group, update_chat_group,
    delete_chat_group, move_chat_to_group, _format_utc_timestamp,
    export_history_package, import_history_package,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/history")
async def get_history_endpoint():
    return await list_chats()


@router.get("/api/history/search")
async def search_history_endpoint(q: str = Query(..., min_length=1, max_length=200)):
    """Full-text search across all chat messages using FTS5."""
    async with await get_session() as session:
        try:
            result = await session.execute(
                sql_text(
                    "SELECT DISTINCT cs.id, cs.title, cs.group_id, cs.updated_at "
                    "FROM chat_messages_fts fts "
                    "JOIN chat_sessions cs ON cs.id = fts.session_id "
                    "WHERE chat_messages_fts MATCH :query "
                    "ORDER BY cs.updated_at DESC LIMIT 20"
                ),
                {"query": q},
            )
            rows = result.fetchall()
            return [
                {
                    "id": row[0],
                    "title": row[1],
                    "group_id": row[2],
                    "timestamp": _format_utc_timestamp(row[3]),
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning("FTS search failed, falling back to title search: %s", e)
            # Fallback: search by title only
            all_chats = await list_chats()
            q_lower = q.lower()
            return [c for c in all_chats if q_lower in (c.get("title", "").lower())]


@router.get("/api/history/groups")
async def get_chat_groups_endpoint():
    return await list_chat_groups()


@router.post("/api/history/groups")
async def create_chat_group_endpoint(body: dict = Body(default={})):
    title = body.get("title", "新分组") if isinstance(body, dict) else "新分组"
    return await create_chat_group(str(title))


@router.patch("/api/history/groups/{group_id}")
async def update_chat_group_endpoint(group_id: str, body: dict = Body(...)):
    title = body.get("title") if isinstance(body, dict) else None
    is_expanded = body.get("is_expanded") if isinstance(body, dict) else None
    group = await update_chat_group(
        group_id,
        title=str(title) if title is not None else None,
        is_expanded=is_expanded if isinstance(is_expanded, bool) else None,
    )
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.delete("/api/history/groups/{group_id}")
async def delete_chat_group_endpoint(group_id: str):
    if not await delete_chat_group(group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "ok"}


@router.patch("/api/history/{session_id}/group")
async def move_chat_to_group_endpoint(session_id: str, body: dict = Body(...)):
    group_id = body.get("group_id") if isinstance(body, dict) else None
    if group_id == "":
        group_id = None
    moved = await move_chat_to_group(session_id, group_id)
    if not moved:
        raise HTTPException(status_code=404, detail="Chat or group not found")
    return {"status": "ok", "group_id": group_id}


@router.get("/api/history/export/all")
async def export_all_chats(format: str = "markdown"):
    """批量导出所有对话为一个文件。"""
    from fastapi.responses import Response
    import json
    import datetime as _dt

    all_chats = await list_chats()
    if not all_chats:
        raise HTTPException(status_code=404, detail="没有可导出的对话")

    date_str = _dt.datetime.now().strftime("%Y%m%d")

    if format.lower() == "json":
        export_data = await export_history_package()
        content = json.dumps(export_data, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="justsearch-export-{date_str}.json"'},
        )

    # Markdown export
    md_lines = [f"# JustSearch 对话导出\n", f"导出时间: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for chat_summary in all_chats:
        chat_data = await load_chat_history(chat_summary["id"])
        if not chat_data:
            continue
        messages = chat_data.get("messages", [])
        title = chat_data.get("title", "对话")
        md_lines.append(f"\n---\n\n## {title}\n")
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                md_lines.append(f"### 👤 用户\n\n{content}\n")
            elif role == "assistant":
                md_lines.append(f"### 🤖 助手\n\n{content[:2000]}\n")

    return Response(
        content="\n".join(md_lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="justsearch-export-{date_str}.md"'},
    )


@router.post("/api/history/import")
async def import_history_endpoint(body: dict = Body(...)):
    """导入聊天记录 JSON 包。重复的会话和分组会跳过，不覆盖现有数据。"""
    try:
        summary = await import_history_package(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "ok", **summary}


@router.get("/api/history/{session_id}")
async def get_chat_endpoint(session_id: str):
    path = get_chat_path(session_id)
    history = await load_chat_history(path)
    if not history:
        raise HTTPException(status_code=404, detail="Chat not found")
    return history


@router.delete("/api/history/{session_id}")
async def delete_chat_endpoint(session_id: str):
    await delete_chat(session_id)
    return {"status": "ok"}


@router.patch("/api/history/{session_id}")
async def rename_chat_endpoint(session_id: str, body: dict = Body(...)):
    new_title = body.get("title", "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    history_data = await load_chat_history(session_id)
    if not history_data:
        raise HTTPException(status_code=404, detail="Chat not found")
    await save_chat_history(session_id, history_data.get("messages", []), title=new_title)
    return {"status": "ok", "title": new_title}


@router.get("/api/history/{session_id}/export")
async def export_chat(session_id: str, format: str = "markdown"):
    """导出单个对话。支持 markdown (默认) 和 json 格式。"""
    from ..database import load_chat_history, get_chat_path
    from fastapi.responses import Response
    import datetime as _dt

    path = get_chat_path(session_id)
    data = await load_chat_history(path)
    if not data:
        raise HTTPException(status_code=404, detail="对话不存在")

    messages = data.get("messages", [])
    title = data.get("title", "对话导出")
    date_str = _dt.datetime.now().strftime("%Y%m%d")

    if format.lower() == "json":
        # JSON export — full data
        import json
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="chat-{session_id[:8]}-{date_str}.json"'},
        )

    # Markdown export (default)
    md_lines = [f"# {title}\n"]
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            md_lines.append(f"## 👤 用户\n\n{content}\n")
        elif role == "assistant":
            md_lines.append(f"## 🤖 助手\n\n{content}\n")
            sources = msg.get("sources", [])
            if sources:
                md_lines.append("### 参考资料\n")
                for src in sources:
                    url = src.get("url", "")
                    src_title = src.get("title", "")
                    md_lines.append(f"- [{src_title}]({url})")
                md_lines.append("")

    return Response(
        content="\n".join(md_lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chat-{session_id[:8]}-{date_str}.md"'},
    )


@router.delete("/api/history")
async def delete_all_chats_endpoint():
    await delete_all_chats()
    return {"status": "ok"}
