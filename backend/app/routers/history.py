"""
History router – /api/history endpoints
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Body

from ..database import (
    list_chats, load_chat_history, save_chat_history,
    delete_chat, get_chat_path, delete_all_chats,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/history")
async def get_history_endpoint():
    return await list_chats()


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
        # Export all as JSON
        export_data = []
        for chat_summary in all_chats:
            chat_data = await load_chat_history(chat_summary["id"])
            if chat_data:
                export_data.append(chat_data)
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
