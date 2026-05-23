"""Provider configuration helpers for JustSearch."""

from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import HTTPException

from .database import mask_api_key


DEFAULT_PROVIDER_ID = "deepseek"
DEFAULT_PROVIDER = {
    "id": DEFAULT_PROVIDER_ID,
    "name": "DeepSeek",
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "model_id": "deepseek-v4-pro",
}

_PROVIDER_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def normalize_provider(provider: dict[str, Any]) -> dict[str, str]:
    provider_id = str(provider.get("id", "")).strip()
    if not provider_id:
        raise HTTPException(status_code=400, detail="provider id 不能为空")
    if not _PROVIDER_ID_RE.match(provider_id):
        raise HTTPException(
            status_code=400,
            detail="provider id 只能包含字母、数字、下划线和连字符，且必须以字母或数字开头",
        )

    base_url = str(provider.get("base_url", "")).strip()
    model_id = str(provider.get("model_id", "")).strip()
    if not base_url:
        raise HTTPException(status_code=400, detail=f"provider {provider_id} 缺少 base_url")
    if not model_id:
        raise HTTPException(status_code=400, detail=f"provider {provider_id} 缺少 model_id")

    return {
        "id": provider_id,
        "name": str(provider.get("name", "")).strip() or provider_id,
        "api_key": str(provider.get("api_key", "")).strip(),
        "base_url": base_url,
        "model_id": model_id,
    }


def normalize_providers(
    providers: list[dict[str, Any]] | None,
    *,
    current_providers: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    if not providers:
        raise HTTPException(status_code=400, detail="至少需要配置一个 provider")

    current_by_id = {
        str(provider.get("id", "")).strip(): provider
        for provider in (current_providers or [])
        if str(provider.get("id", "")).strip()
    }
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []

    for provider in providers:
        item = normalize_provider(provider)
        provider_id = item["id"]
        if provider_id in seen:
            raise HTTPException(status_code=400, detail=f"重复的 provider id: {provider_id}")
        seen.add(provider_id)

        if "****" in item["api_key"]:
            item["api_key"] = str(current_by_id.get(provider_id, {}).get("api_key", "")).strip()

        normalized.append(item)

    return normalized


def ensure_default_provider_id(
    providers: list[dict[str, Any]],
    default_provider_id: str | None,
) -> str:
    provider_ids = {str(provider.get("id", "")).strip() for provider in providers}
    requested = (default_provider_id or "").strip()

    if not requested:
        return str(providers[0]["id"])
    if requested not in provider_ids:
        raise HTTPException(status_code=400, detail=f"默认 provider 不存在: {requested}")
    return requested


def mask_provider_secrets(settings: dict[str, Any]) -> dict[str, Any]:
    safe_settings = copy.deepcopy(settings)
    providers = safe_settings.get("providers")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, dict) and provider.get("api_key"):
                provider["api_key"] = mask_api_key(str(provider["api_key"]))
    return safe_settings


def get_provider_by_id(settings: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    for provider in settings.get("providers") or []:
        if str(provider.get("id", "")).strip() == provider_id:
            return provider
    return None


def first_model_id(model_ids: str) -> str:
    raw = str(model_ids or "").strip()
    if "," in raw:
        raw = next((item.strip() for item in raw.split(",") if item.strip()), raw)
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    return raw
