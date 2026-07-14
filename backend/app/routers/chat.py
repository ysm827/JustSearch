"""
Chat router – /api/chat
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..database import (
    load_settings, save_chat_history, load_chat_history, get_chat_path, get_next_api_key,
    delete_message, truncate_messages_from, normalize_route_safe_id,
)
from ..providers import (
    WORKFLOW_MODEL_STEP_IDS,
    first_model_id,
    get_provider_by_id,
    is_unsupported_model_id,
    require_provider_api_key,
)
from ..workflow import SearchWorkflow
from ..logging_utils import set_request_id
from ..search_engine import get_all_engines
from ..extension_bridge import get_ws_endpoint, is_extension_connected
from ..citation_evidence import (
    build_citation_evidences,
    client_source_payload,
    strip_source_content_for_storage,
)

logger = logging.getLogger(__name__)

router = APIRouter()

BRIDGE_REQUIRED_DETAIL = {
    "code": "BRIDGE_REQUIRED",
    "message": (
        "JustSearch 浏览器桥接不可用：Chrome 扩展未连接。"
        "请安装并启用 JustSearch Bridge 扩展后再试。"
    ),
    "download_url": "/api/extension/download",
}


def _require_extension_bridge() -> None:
    """Fail fast before starting a search workflow when the Chrome bridge is offline."""
    if is_extension_connected():
        return
    detail = {
        **BRIDGE_REQUIRED_DETAIL,
        "ws_url": get_ws_endpoint(),
    }
    raise HTTPException(status_code=503, detail=detail)


async def _cancel_and_drain_tasks(tasks: list[asyncio.Task]) -> list[Any]:
    if not tasks:
        return []
    for task in tasks:
        if not task.done():
            task.cancel()
    return await asyncio.gather(*tasks, return_exceptions=True)


def _task_terminal_exception(task: asyncio.Task) -> BaseException | None:
    if task.cancelled():
        return asyncio.CancelledError()
    try:
        return task.exception()
    except asyncio.CancelledError as exc:
        return exc


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    query: str
    provider_id: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    search_engine: Optional[str] = None
    max_results: Optional[int] = None
    max_iterations: Optional[int] = None
    interactive_search: Optional[bool] = None
    live_artifacts_mode: Optional[bool] = None
    canvas_mode: Optional[bool] = None
    # AMC-style resend/edit: drop this message index and everything after it
    # before running the workflow and saving the new user+assistant turn.
    truncate_from_index: Optional[int] = None


async def _resolve_workflow_step_models(
    settings: dict,
    fallback_provider_id: str,
    fallback_api_key: str,
    fallback_model: str,
) -> dict[str, dict[str, str]]:
    step_settings = settings.get("workflow_step_models") or {}
    provider_key_cache: dict[str, str] = {fallback_provider_id: fallback_api_key}
    resolved: dict[str, dict[str, str]] = {}

    for step_id in WORKFLOW_MODEL_STEP_IDS:
        raw_step = step_settings.get(step_id) if isinstance(step_settings, dict) else {}
        if not isinstance(raw_step, dict):
            raw_step = {}

        configured_provider_id = str(raw_step.get("provider_id") or "").strip()
        provider_id = configured_provider_id or fallback_provider_id
        provider = get_provider_by_id(settings, provider_id)
        if not provider:
            raise HTTPException(status_code=400, detail=f"步骤 {step_id} 的 provider 不存在: {provider_id}")
        require_provider_api_key(provider, f"步骤 {step_id} 的 provider")

        configured_model = raw_step.get("model_id") or raw_step.get("model") or ""
        model_source = configured_model
        if not model_source:
            model_source = (
                fallback_model
                if not configured_provider_id or configured_provider_id == fallback_provider_id
                else provider.get("model_id", "")
            )
        model = first_model_id(model_source)
        if not model:
            raise HTTPException(status_code=400, detail=f"步骤 {step_id} 缺少模型配置")
        if is_unsupported_model_id(model):
            raise HTTPException(status_code=400, detail="Gemini 2.5 系列模型不再支持")

        if provider_id not in provider_key_cache:
            raw_api_key = str(provider.get("api_key", "")).strip()
            if raw_api_key:
                raw_api_key = await get_next_api_key(raw_api_key)
            provider_key_cache[provider_id] = raw_api_key

        api_key = provider_key_cache[provider_id]

        resolved[step_id] = {
            "provider_id": provider_id,
            "api_key": api_key,
            "base_url": provider.get("base_url", ""),
            "model": model,
        }

    return resolved


def _safe_step_model_meta(step_model_configs: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        step_id: {
            "provider_id": config.get("provider_id", ""),
            "model": config.get("model", ""),
        }
        for step_id, config in step_model_configs.items()
    }


def _client_source_payload(
    sources: list[dict[str, Any]],
    *,
    query_hint: str | None = None,
) -> list[dict[str, Any]]:
    """SSE-safe source payload with snippet/excerpt (no full crawl body)."""
    return client_source_payload(sources, query_hint=query_hint)


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)


def _text_setting(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _resolve_search_engine(requested: str | None, saved: str | None) -> str:
    valid_engines = set(get_all_engines())
    requested_engine = _text_setting(requested)
    saved_engine = _text_setting(saved)
    engine = requested_engine or saved_engine or "google"
    if requested_engine and engine not in valid_engines:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的搜索引擎。可选: {', '.join(sorted(valid_engines))}",
        )
    if engine in valid_engines:
        return engine
    return "google"


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@router.post("/api/chat")
async def chat_endpoint(http_request: Request, request: ChatRequest):
    # Set request ID for log correlation
    import uuid
    set_request_id(uuid.uuid4().hex[:8])

    query_text = request.query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="query 不能为空")

    raw_session_id = str(request.session_id or "").strip()
    if raw_session_id:
        session_id = normalize_route_safe_id(raw_session_id)
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 格式无效")
    else:
        session_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]

    defaults = await load_settings()
    provider_id = request.provider_id.strip()
    provider = get_provider_by_id(defaults, provider_id)
    if not provider:
        raise HTTPException(status_code=400, detail=f"未找到 provider: {provider_id}")
    require_provider_api_key(provider)

    api_key = provider.get("api_key", "")

    if api_key:
        api_key = await get_next_api_key(api_key)

    base_url = provider.get("base_url")
    model = first_model_id(request.model or provider.get("model_id", ""))
    if is_unsupported_model_id(model):
        raise HTTPException(status_code=400, detail="Gemini 2.5 系列模型不再支持")
    workflow_step_models = await _resolve_workflow_step_models(defaults, provider_id, api_key, model)

    search_engine = _resolve_search_engine(request.search_engine, defaults.get("search_engine", "google"))
    max_results = _bounded_int(
        request.max_results if request.max_results is not None else defaults.get("max_results", 50),
        default=50,
        minimum=1,
        maximum=50,
    )
    max_iterations = _bounded_int(
        request.max_iterations if request.max_iterations is not None else defaults.get("max_iterations", 5),
        default=5,
        minimum=1,
        maximum=10,
    )
    interactive_search = (
        _coerce_bool(request.interactive_search)
        if request.interactive_search is not None
        else _coerce_bool(defaults.get("interactive_search"), True)
    )
    saved_live_artifacts_mode = _coerce_bool(defaults.get("live_artifacts_mode"), False)
    live_artifacts_mode = (
        _coerce_bool(request.live_artifacts_mode)
        if request.live_artifacts_mode is not None
        else saved_live_artifacts_mode
    )
    if request.canvas_mode:
        live_artifacts_mode = True

    # All engines scrape via the real-Chrome extension bridge.
    _require_extension_bridge()

    logger.info("[Chat] New request: session=%s, provider=%s, query='%s', engine=%s, model=%s",
                session_id, provider_id, query_text[:80], search_engine, model)

    try:
        workflow = SearchWorkflow(
            api_key, base_url, model, search_engine, max_results,
            max_iterations, interactive_search,
            session_id=session_id,
            step_model_configs=workflow_step_models,
            live_artifacts_mode=live_artifacts_mode,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    chat_path = get_chat_path(session_id)
    chat_history_data = await load_chat_history(chat_path)
    context_messages = chat_history_data.get("messages", []) if chat_history_data else []

    # AMC resend/retry: truncate history at the edited user message (or the user
    # message preceding an assistant retry) so context and persistence match.
    truncate_from_index = request.truncate_from_index
    if truncate_from_index is not None:
        try:
            truncate_from_index = int(truncate_from_index)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="truncate_from_index 必须是整数")
        if truncate_from_index < 0:
            raise HTTPException(status_code=400, detail="truncate_from_index 不能为负数")
        if raw_session_id:
            truncated = await truncate_messages_from(session_id, truncate_from_index)
            if not truncated:
                raise HTTPException(status_code=404, detail="会话不存在，无法截断消息")
            chat_history_data = await load_chat_history(chat_path)
            context_messages = chat_history_data.get("messages", []) if chat_history_data else []
        else:
            # Brand-new session has no history; ignore truncate.
            truncate_from_index = None

    async def event_generator():
        yield f"data: {json.dumps({'type': 'meta', 'session_id': session_id, 'provider_id': provider_id, 'model': model, 'step_models': _safe_step_model_meta(workflow_step_models)})}\n\n"

        queue = asyncio.Queue()
        logs = []
        accumulated_sources = []
        final_stats = {}

        def progress_callback(msg):
            logs.append(msg)
            queue.put_nowait({"type": "log", "content": msg})

        def stream_callback(chunk):
            queue.put_nowait({"type": "answer_chunk", "content": chunk})

        def source_callback(sources):
            accumulated_sources[:] = list(sources or [])
            queue.put_nowait({
                "type": "sources",
                "content": _client_source_payload(accumulated_sources, query_hint=query_text),
            })

        def stats_callback(stats):
            nonlocal final_stats
            final_stats = stats
            queue.put_nowait({"type": "stats", "content": stats})

        task = asyncio.create_task(
            workflow.run(query_text, progress_callback, stream_callback,
                         context_messages, source_callback, stats_callback)
        )

        try:
            while not task.done():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(item)}\n\n"

                    while not queue.empty():
                        try:
                            extra = queue.get_nowait()
                            yield f"data: {json.dumps(extra)}\n\n"
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

            task_error = _task_terminal_exception(task)
            if task_error:
                error_content = (
                    "请求已取消"
                    if isinstance(task_error, asyncio.CancelledError)
                    else str(task_error) or task_error.__class__.__name__
                )
                yield f"data: {json.dumps({'type': 'error', 'content': error_content})}\n\n"
                return

            while not queue.empty():
                try:
                    item = queue.get_nowait()
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.QueueEmpty:
                    break

            result = task.result()
            answer_text = result if isinstance(result, str) else str(result or "")
            slim_sources = strip_source_content_for_storage(
                accumulated_sources, query_hint=query_text
            )
            citations = build_citation_evidences(answer_text, accumulated_sources)

            try:
                path = get_chat_path(session_id)
                existing_data = await load_chat_history(path)
                existing_messages = existing_data.get("messages", []) if existing_data else []

                new_messages = [
                    {"role": "user", "content": query_text},
                    {
                        "role": "assistant",
                        "content": answer_text,
                        "logs": logs,
                        # Persist slim sources + citation evidence (no full crawl bodies).
                        "sources": slim_sources,
                        "citations": citations,
                        "stats": final_stats,
                    },
                ]

                full_messages = existing_messages + new_messages
                title = existing_data.get("title") if existing_data else None
                
                # Auto-generate title from first user message if not set
                if not title and not existing_messages:
                    title = query_text[:50]
                    if len(query_text) > 50:
                        # Try to break at a sentence boundary
                        last_punct = max(title.rfind('。'), title.rfind('.'), title.rfind('？'), title.rfind('?'), title.rfind('！'), title.rfind('!'))
                        if last_punct > 10:
                            title = title[:last_punct]
                        else:
                            title = title + "..."
                
                await save_chat_history(session_id, full_messages, title)

                yield f"data: {json.dumps({'type': 'answer', 'content': answer_text, 'session_id': session_id, 'sources': slim_sources, 'citations': citations}, ensure_ascii=False)}\n\n"

            except Exception as e:
                logger.error("Failed to save chat history for %s: %s", session_id, e)
                # Still yield the answer even if saving fails
                yield f"data: {json.dumps({'type': 'answer', 'content': answer_text, 'session_id': session_id, 'sources': slim_sources, 'citations': citations}, ensure_ascii=False)}\n\n"

            yield "data: [DONE]\n\n"

        except asyncio.CancelledError:
            logger.info("Task cancelled by client disconnect: %s", session_id)
            task.cancel()
            raise
        finally:
            if not task.done():
                logger.info("Cleaning up running task: %s", session_id)
            await _cancel_and_drain_tasks([task])

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class DeleteMessageRequest(BaseModel):
    session_id: str
    message_index: int  # 0-based


@router.delete("/api/chat/message")
async def delete_message_endpoint(request: DeleteMessageRequest):
    """Delete a single message from a chat session by index."""
    session_id = normalize_route_safe_id(request.session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 格式无效")
    ok = await delete_message(session_id, request.message_index)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "ok"}
