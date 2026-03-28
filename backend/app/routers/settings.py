"""
Settings router – /api/settings endpoints and /api/clear-cache
"""

import logging
import os
import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..database import (
    load_settings, save_settings, delete_all_chats,
    DEFAULT_SETTINGS, mask_api_key, get_chat_path, load_chat_history,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SENSITIVE_FIELDS = {"api_key"}


class SettingsModel(BaseModel):
    theme: Optional[str] = "light"
    api_key: Optional[str] = ""
    base_url: Optional[str] = ""
    model_id: Optional[str] = ""
    search_engine: Optional[str] = "duckduckgo"
    max_results: Optional[int] = 8
    max_iterations: Optional[int] = 5
    interactive_search: Optional[bool] = True
    max_concurrent_pages: Optional[int] = 10
    max_context_turns: Optional[int] = 6


@router.get("/api/settings")
async def get_settings_endpoint():
    settings = await load_settings()
    for field in _SENSITIVE_FIELDS:
        if field in settings and settings[field]:
            settings[field] = mask_api_key(settings[field])
    return settings


@router.get("/api/settings/default")
def get_default_settings_endpoint():
    settings = DEFAULT_SETTINGS.copy()
    for field in _SENSITIVE_FIELDS:
        if field in settings and settings[field]:
            settings[field] = mask_api_key(settings[field])
    return settings


@router.post("/api/settings")
async def update_settings_endpoint(settings: SettingsModel):
    current = await load_settings()
    new_settings = settings.model_dump(exclude_none=True)

    update = {}
    for k, v in new_settings.items():
        if v == "":
            continue
        update[k] = v

    incoming_key = new_settings.get("api_key", "")
    if incoming_key and "****" in incoming_key:
        update["api_key"] = current.get("api_key", "")

    current.update(update)

    if await save_settings(current):
        for field in _SENSITIVE_FIELDS:
            if field in current and current[field]:
                current[field] = mask_api_key(current[field])
        return {"status": "ok", "settings": current}
    raise HTTPException(status_code=500, detail="Failed to save settings")


@router.post("/api/clear-cache")
async def clear_cache_endpoint():
    """清除所有缓存：聊天记录 + 浏览器数据 + 重置设置。"""

    # 1. 删除所有聊天记录
    await delete_all_chats()

    # 2. 删除浏览器持久化数据
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    user_data_dir = os.path.join(project_root, "user_data")
    if os.path.exists(user_data_dir):
        shutil.rmtree(user_data_dir, ignore_errors=True)
        os.makedirs(user_data_dir, exist_ok=True)

    # 3. 重置设置为默认值 – wipe all settings rows
    from ..database import Settings, get_session
    from sqlalchemy import delete
    async with await get_session() as session:
        await session.execute(delete(Settings))
        await session.commit()

    return {"status": "ok"}
