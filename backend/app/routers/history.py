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


@router.delete("/api/history")
async def delete_all_chats_endpoint():
    await delete_all_chats()
    return {"status": "ok"}
