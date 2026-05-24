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
WORKFLOW_MODEL_STEPS = [
    {"id": "analysis", "name": "问题分析"},
    {"id": "relevance", "name": "相关性评估"},
    {"id": "interaction", "name": "页面交互"},
    {"id": "answer", "name": "最终回答"},
]
WORKFLOW_MODEL_STEP_IDS = [step["id"] for step in WORKFLOW_MODEL_STEPS]
DEFAULT_WORKFLOW_STEP_MODELS = {
    step_id: {"provider_id": "", "model_id": ""}
    for step_id in WORKFLOW_MODEL_STEP_IDS
}

_PROVIDER_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_UNSUPPORTED_MODEL_RE = re.compile(r"\bgemini[\s._-]*2[\s._-]*5\b", re.IGNORECASE)


def is_unsupported_model_id(model_id: Any) -> bool:
    """Return True for model ids/display names that are no longer supported."""
    return bool(_UNSUPPORTED_MODEL_RE.search(str(model_id or "")))


def supported_model_items(model_ids: Any) -> list[str]:
    items = [
        item.strip()
        for item in str(model_ids or "").split(",")
        if item.strip()
    ]
    return [item for item in items if not is_unsupported_model_id(item)]


def normalize_supported_model_ids(model_ids: Any) -> str:
    return ", ".join(supported_model_items(model_ids))


def with_supported_provider_models(provider: dict[str, Any]) -> dict[str, Any]:
    item = provider.copy()
    item["model_id"] = normalize_supported_model_ids(item.get("model_id", ""))
    return item


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
    model_id = normalize_supported_model_ids(provider.get("model_id", ""))
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
            previous_provider_id = str(provider.get("previous_id", "")).strip()
            current_provider = current_by_id.get(provider_id)
            if current_provider is None and previous_provider_id:
                current_provider = current_by_id.get(previous_provider_id)
            item["api_key"] = str((current_provider or {}).get("api_key", "")).strip()

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


def _available_model_pairs(providers: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for provider in providers:
        provider_id = str(provider.get("id", "")).strip()
        if not provider_id:
            continue
        for model in supported_model_items(provider.get("model_id", "")):
            model_id = first_model_id(model)
            if model_id:
                pairs.add((provider_id, model_id))
    return pairs


def normalize_workflow_step_models(
    step_models: dict[str, Any] | None,
    providers: list[dict[str, Any]],
    *,
    strict: bool = True,
) -> dict[str, dict[str, str]]:
    """Validate per-workflow-step model selections against configured providers."""
    normalized = copy.deepcopy(DEFAULT_WORKFLOW_STEP_MODELS)
    if not isinstance(step_models, dict):
        return normalized

    available_pairs = _available_model_pairs(providers)
    for step_id in WORKFLOW_MODEL_STEP_IDS:
        raw = step_models.get(step_id) or {}
        if not isinstance(raw, dict):
            if strict:
                raise HTTPException(status_code=400, detail=f"步骤模型配置无效: {step_id}")
            continue

        provider_id = str(raw.get("provider_id", "")).strip()
        model_id = first_model_id(raw.get("model_id") or raw.get("model") or "")
        if not provider_id and not model_id:
            continue
        if not provider_id or not model_id:
            if strict:
                raise HTTPException(status_code=400, detail=f"步骤 {step_id} 的模型配置不完整")
            continue
        if (provider_id, model_id) not in available_pairs:
            if strict:
                raise HTTPException(
                    status_code=400,
                    detail=f"步骤 {step_id} 选择的模型不存在: {provider_id}/{model_id}",
                )
            continue

        normalized[step_id] = {
            "provider_id": provider_id,
            "model_id": model_id,
        }

    return normalized


def mask_provider_secrets(settings: dict[str, Any]) -> dict[str, Any]:
    safe_settings = copy.deepcopy(settings)
    if safe_settings.get("model_id"):
        safe_settings["model_id"] = normalize_supported_model_ids(safe_settings["model_id"])
    if safe_settings.get("api_key"):
        safe_settings["api_key"] = mask_api_key(str(safe_settings["api_key"]))

    providers = safe_settings.get("providers")
    if isinstance(providers, list):
        for index, provider in enumerate(providers):
            if isinstance(provider, dict) and provider.get("api_key"):
                provider["api_key"] = mask_api_key(str(provider["api_key"]))
            if isinstance(provider, dict):
                providers[index] = with_supported_provider_models(provider)
    return safe_settings


def get_provider_by_id(settings: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    for provider in settings.get("providers") or []:
        if str(provider.get("id", "")).strip() == provider_id:
            return with_supported_provider_models(provider)
    return None


def first_model_id(model_ids: str) -> str:
    items = supported_model_items(model_ids)
    raw = items[0] if items else str(model_ids or "").strip()
    if "," in raw:
        raw = next((item.strip() for item in raw.split(",") if item.strip()), raw)
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    return raw
