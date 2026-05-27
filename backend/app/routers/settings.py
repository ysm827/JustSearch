"""
Settings router – /api/settings endpoints and /api/clear-cache
"""

import asyncio
import logging
import os
import shutil
import time

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Optional

from ..database import (
    load_settings, save_settings, delete_all_chats,
    DEFAULT_SETTINGS, get_chat_path, load_chat_history,
)
from ..browser_manager import BrowserManager
from ..engine_health import engine_health
from ..llm_client import _provider_error_message
from ..openai_client import create_openai_client
from ..providers import (
    ensure_default_provider_id,
    get_provider_by_id,
    is_local_provider_base_url,
    is_unsupported_model_id,
    mask_provider_secrets,
    normalize_workflow_step_models,
    normalize_providers,
    split_model_item,
)
from ..search_engine import get_all_engines

logger = logging.getLogger(__name__)

router = APIRouter()

_ENGINE_CHECK_QUERY = "JustSearch test"
_ENGINE_CHECK_TIMEOUT_SECONDS = 75.0
_ENGINE_FAILURE_MESSAGES = {
    "batch_soft_timeout": "批次内部分查询超时",
    "selector": "结果选择器未匹配",
    "low_quality": "结果相关性过低",
    "timeout": "检测超时",
    "blocked": "验证/反爬页面",
    "captcha": "检测到验证码",
    "other": "检测失败",
}


class ProviderModel(BaseModel):
    id: str
    previous_id: Optional[str] = ""
    name: Optional[str] = ""
    api_key: Optional[str] = ""
    base_url: str
    model_id: str


class WorkflowStepModel(BaseModel):
    provider_id: Optional[str] = ""
    model_id: Optional[str] = ""


class SettingsModel(BaseModel):
    theme: Optional[str] = "light"
    default_provider_id: Optional[str] = None
    providers: Optional[list[ProviderModel]] = None
    workflow_step_models: Optional[dict[str, WorkflowStepModel]] = None
    search_engine: Optional[str] = "searxng"
    max_results: Optional[int] = 50
    max_iterations: Optional[int] = 5
    interactive_search: Optional[bool] = True
    live_artifacts_mode: Optional[bool] = False
    max_concurrent_pages: Optional[int] = 10


class EngineCheckRequest(BaseModel):
    query: Optional[str] = _ENGINE_CHECK_QUERY


@router.get("/api/settings")
async def get_settings_endpoint():
    settings = await load_settings()
    return mask_provider_secrets(settings)


@router.get("/api/settings/default")
def get_default_settings_endpoint():
    settings = DEFAULT_SETTINGS.copy()
    return mask_provider_secrets(settings)


@router.post("/api/settings")
async def update_settings_endpoint(settings: SettingsModel):
    current = await load_settings()
    new_settings = settings.model_dump(exclude_unset=True, exclude_none=True)

    update = {}
    for k, v in new_settings.items():
        if v == "" and k != "default_provider_id":
            continue
        update[k] = v

    if "providers" in update:
        providers = normalize_providers(
            update["providers"],
            current_providers=current.get("providers", []),
        )
        update["providers"] = providers
        update["default_provider_id"] = ensure_default_provider_id(
            providers,
            update.get("default_provider_id") or current.get("default_provider_id"),
        )
        primary_provider = get_provider_by_id(
            {"providers": providers},
            update["default_provider_id"],
        ) or providers[0]
        update["api_key"] = primary_provider.get("api_key", "")
        update["base_url"] = primary_provider.get("base_url", "")
        update["model_id"] = primary_provider.get("model_id", "")
    elif "default_provider_id" in update:
        update["default_provider_id"] = ensure_default_provider_id(
            current.get("providers", []),
            update["default_provider_id"],
        )
        primary_provider = get_provider_by_id(
            {"providers": current.get("providers", [])},
            update["default_provider_id"],
        )
        if primary_provider:
            update["api_key"] = primary_provider.get("api_key", "")
            update["base_url"] = primary_provider.get("base_url", "")
            update["model_id"] = primary_provider.get("model_id", "")

    providers_for_steps = update.get("providers") or current.get("providers", [])
    if "workflow_step_models" in update:
        update["workflow_step_models"] = normalize_workflow_step_models(
            update["workflow_step_models"],
            providers_for_steps,
        )
    elif "providers" in update:
        update["workflow_step_models"] = normalize_workflow_step_models(
            current.get("workflow_step_models"),
            providers_for_steps,
            strict=False,
        )

    # Validate numeric ranges
    if "max_results" in update:
        update["max_results"] = max(1, min(50, int(update["max_results"])))
    if "max_iterations" in update:
        update["max_iterations"] = max(1, min(10, int(update["max_iterations"])))
    if "max_concurrent_pages" in update:
        update["max_concurrent_pages"] = max(1, min(20, int(update["max_concurrent_pages"])))

    # Validate search engine
    valid_engines = set(get_all_engines())
    if "search_engine" in update and update["search_engine"] not in valid_engines:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的搜索引擎。可选: {', '.join(sorted(valid_engines))}",
        )

    current.update(update)

    if await save_settings(current):
        return {"status": "ok", "settings": mask_provider_secrets(current)}
    raise HTTPException(status_code=500, detail="Failed to save settings")


async def _check_single_engine(engine: str, query: str) -> dict:
    manager = BrowserManager(engine=engine, max_results=3)
    started_at = time.time()
    try:
        results = await asyncio.wait_for(
            manager.search_web(
                query,
                allow_fallback=False,
                use_cache=False,
            ),
            timeout=_ENGINE_CHECK_TIMEOUT_SECONDS,
        )
        result_count = len(results)
        reason = "" if result_count > 0 else engine_health.get_latest_failure_reason(
            engine,
            since=started_at,
        )
        return {
            "engine": engine,
            "available": result_count > 0,
            "result_count": result_count,
            "reason": reason,
            "error": "" if result_count > 0 else _ENGINE_FAILURE_MESSAGES.get(
                reason,
                "未解析到搜索结果",
            ),
        }
    except asyncio.TimeoutError:
        engine_health.record(engine, success=False, reason="timeout")
        return {
            "engine": engine,
            "available": False,
            "result_count": 0,
            "reason": "timeout",
            "error": "检测超时",
        }
    except Exception as e:
        logger.warning("Search engine check failed for %s: %s", engine, e)
        engine_health.record(engine, success=False, reason="other")
        return {
            "engine": engine,
            "available": False,
            "result_count": 0,
            "reason": "other",
            "error": str(e)[:200] or "检测失败",
        }


@router.post("/api/settings/check-engines")
async def check_search_engines_endpoint(body: EngineCheckRequest | None = None):
    query = (body.query if body else _ENGINE_CHECK_QUERY) or _ENGINE_CHECK_QUERY
    query = query.strip() or _ENGINE_CHECK_QUERY
    engines = get_all_engines()
    results = await asyncio.gather(
        *(_check_single_engine(engine, query) for engine in engines)
    )
    return {"query": query, "results": results}


@router.post("/api/settings/validate-key")
async def validate_api_key_endpoint(body: dict = Body(...)):
    """Validate an API key by making a test request to the model endpoint."""
    api_key = body.get("api_key", "").strip()
    if api_key and "****" in api_key:
        provider_id = body.get("provider_id", "").strip()
        previous_provider_id = body.get("previous_provider_id", "").strip()
        settings = await load_settings()
        provider = get_provider_by_id(settings, provider_id) if provider_id else None
        if provider is None and previous_provider_id:
            provider = get_provider_by_id(settings, previous_provider_id)
        api_key = (provider or {}).get("api_key", "").strip()

    base_url = body.get("base_url", "").strip() or "https://api.openai.com/v1"
    model_id = body.get("model_id", "").strip()
    model_id, _display_name = split_model_item(model_id)

    if not model_id:
        return {"valid": False, "error": "Model ID is empty"}
    if is_unsupported_model_id(model_id):
        return {"valid": False, "error": "Gemini 2.5 系列模型不再支持"}
    if not api_key and not is_local_provider_base_url(base_url):
        return {"valid": False, "error": "请先填写 API 密钥"}

    try:
        client = create_openai_client(api_key=api_key, base_url=base_url)
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
        provider_message = _provider_error_message(e)
        if provider_message:
            return {"valid": False, "error": provider_message}
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
