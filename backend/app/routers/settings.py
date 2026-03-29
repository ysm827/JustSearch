"""
Settings router – /api/settings endpoints and /api/clear-cache
"""

import asyncio
import logging
import os
import shutil

from fastapi import APIRouter, HTTPException, Body
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

    # Validate numeric ranges
    if "max_results" in update:
        update["max_results"] = max(1, min(20, int(update["max_results"])))
    if "max_iterations" in update:
        update["max_iterations"] = max(1, min(10, int(update["max_iterations"])))
    if "max_concurrent_pages" in update:
        update["max_concurrent_pages"] = max(1, min(20, int(update["max_concurrent_pages"])))
    if "max_context_turns" in update:
        update["max_context_turns"] = max(1, min(20, int(update["max_context_turns"])))

    # Validate search engine
    valid_engines = {"duckduckgo", "google", "bing", "sogou", "brave", "searxng"}
    if "search_engine" in update and update["search_engine"] not in valid_engines:
        raise HTTPException(status_code=400, detail=f"不支持的搜索引擎。可选: {', '.join(valid_engines)}")

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


@router.post("/api/settings/validate-key")
async def validate_api_key_endpoint(body: dict = Body(...)):
    """Validate an API key by making a test request to the model endpoint."""
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip() or "https://api.openai.com/v1"
    model_id = body.get("model_id", "").strip()

    if not api_key:
        return {"valid": False, "error": "API key is empty"}

    if not model_id:
        return {"valid": False, "error": "Model ID is empty"}

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        # Make a minimal request to validate
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            ),
            timeout=15.0,
        )
        return {"valid": True, "model": model_id}
    except asyncio.TimeoutError:
        return {"valid": False, "error": "请求超时 (15秒)"}
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            return {"valid": False, "error": "API 密钥无效或已过期"}
        elif "404" in error_msg or "model" in error_msg.lower():
            return {"valid": False, "error": f"模型不存在: {model_id}"}
        elif "429" in error_msg:
            return {"valid": True, "error": "密钥有效但触发限流"}  # Key works, just rate limited
        else:
            return {"valid": False, "error": error_msg[:200]}


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
