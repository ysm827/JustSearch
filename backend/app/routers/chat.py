"""
Chat router – /api/chat and /ws/browser/{session_id}
"""

import asyncio
import json
import logging
import base64
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth import authorize_websocket
from ..database import (
    load_settings, save_chat_history, load_chat_history, get_chat_path, get_next_api_key,
    delete_message,
)
from ..providers import (
    WORKFLOW_MODEL_STEP_IDS,
    first_model_id,
    get_provider_by_id,
    is_unsupported_model_id,
    require_provider_api_key,
)
from ..workflow import SearchWorkflow
from ..interaction import get_interaction_session, mark_interaction_completed
from ..rate_limiter import chat_limiter
from ..logging_utils import set_request_id
from ..search_engine import get_all_engines

logger = logging.getLogger(__name__)

router = APIRouter()


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
    max_concurrent_pages: Optional[int] = None
    live_artifacts_mode: Optional[bool] = None
    canvas_mode: Optional[bool] = None


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


def _resolve_search_engine(requested: str | None, saved: str | None) -> str:
    valid_engines = set(get_all_engines())
    engine = (requested or saved or "searxng").strip()
    if requested and engine not in valid_engines:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的搜索引擎。可选: {', '.join(sorted(valid_engines))}",
        )
    if engine in valid_engines:
        return engine
    return "searxng"


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws/browser/{session_id}")
async def browser_control_endpoint(websocket: WebSocket, session_id: str):
    if not await authorize_websocket(websocket):
        return
    await websocket.accept()

    session = get_interaction_session(session_id)
    if not session:
        await websocket.close(code=4004, reason="No active interaction session")
        return

    page = session["page"]

    async def send_frames():
        try:
            while True:
                if websocket.client_state.name != "CONNECTED":
                    break
                try:
                    screenshot = await page.screenshot(type="jpeg", quality=50)
                    b64_img = base64.b64encode(screenshot).decode("utf-8")
                    await websocket.send_json({"type": "frame", "image": b64_img})
                except Exception as e:
                    logger.error("Frame error: %s", e)
                    break
                await asyncio.sleep(0.5)
        except Exception:
            pass

    async def receive_events():
        try:
            while True:
                data = await websocket.receive_json()
                action = data.get("action")

                if action == "click":
                    x = data.get("x")
                    y = data.get("y")
                    if x is not None and y is not None:
                        try:
                            await page.mouse.click(x, y)
                        except Exception:
                            pass

                elif action == "scroll":
                    delta_y = data.get("dy", 0)
                    try:
                        await page.mouse.wheel(0, delta_y)
                    except Exception:
                        pass

                elif action == "type":
                    text = data.get("text")
                    if text:
                        try:
                            await page.keyboard.type(text)
                        except Exception:
                            pass

                elif action == "key":
                    key = data.get("key")
                    if key:
                        try:
                            await page.keyboard.press(key)
                        except Exception:
                            pass

                elif action == "complete":
                    await mark_interaction_completed(session_id)
                    await websocket.send_json({"type": "status", "msg": "Completed"})
                    break

        except Exception as e:
            logger.error("Input error: %s", e)

    tasks = [
        asyncio.create_task(send_frames()),
        asyncio.create_task(receive_events()),
    ]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@router.post("/api/chat")
async def chat_endpoint(http_request: Request, request: ChatRequest):
    # Set request ID for log correlation
    import uuid
    set_request_id(uuid.uuid4().hex[:8])

    # Rate limiting
    client_host = http_request.client.host if http_request.client else "global"
    allowed, retry_after = chat_limiter.check(client_host)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请在 {retry_after} 秒后重试。",
        )

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

    search_engine = _resolve_search_engine(request.search_engine, defaults.get("search_engine", "searxng"))
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
        request.interactive_search
        if request.interactive_search is not None
        else defaults.get("interactive_search", True)
    )
    max_concurrent_pages = _bounded_int(
        request.max_concurrent_pages if request.max_concurrent_pages is not None else defaults.get("max_concurrent_pages", 3),
        default=3,
        minimum=1,
        maximum=20,
    )
    saved_live_artifacts_mode = _coerce_bool(defaults.get("live_artifacts_mode"), False)
    live_artifacts_mode = (
        _coerce_bool(request.live_artifacts_mode)
        if request.live_artifacts_mode is not None
        else saved_live_artifacts_mode
    )
    if request.canvas_mode:
        live_artifacts_mode = True
    session_id = request.session_id
    if not session_id:
        session_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]

    logger.info("[Chat] New request: session=%s, provider=%s, query='%s', engine=%s, model=%s",
                session_id, provider_id, request.query[:80], search_engine, model)

    try:
        workflow = SearchWorkflow(
            api_key, base_url, model, search_engine, max_results,
            max_iterations, interactive_search,
            session_id=session_id,
            max_concurrent_pages=max_concurrent_pages,
            step_model_configs=workflow_step_models,
            live_artifacts_mode=live_artifacts_mode,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    chat_path = get_chat_path(session_id)
    chat_history_data = await load_chat_history(chat_path)
    context_messages = chat_history_data.get("messages", []) if chat_history_data else []

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
            queue.put_nowait({"type": "sources", "content": sources})

        def stats_callback(stats):
            nonlocal final_stats
            final_stats = stats
            queue.put_nowait({"type": "stats", "content": stats})

        task = asyncio.create_task(
            workflow.run(request.query, progress_callback, stream_callback,
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

            if task.exception():
                yield f"data: {json.dumps({'type': 'error', 'content': str(task.exception())})}\n\n"
                return

            while not queue.empty():
                try:
                    item = queue.get_nowait()
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.QueueEmpty:
                    break

            result = task.result()

            try:
                path = get_chat_path(session_id)
                existing_data = await load_chat_history(path)
                existing_messages = existing_data.get("messages", []) if existing_data else []

                new_messages = [
                    {"role": "user", "content": request.query},
                    {"role": "assistant", "content": result, "logs": logs, "sources": accumulated_sources, "stats": final_stats},
                ]

                full_messages = existing_messages + new_messages
                title = existing_data.get("title") if existing_data else None
                
                # Auto-generate title from first user message if not set
                if not title and not existing_messages:
                    title = request.query[:50]
                    if len(request.query) > 50:
                        # Try to break at a sentence boundary
                        last_punct = max(title.rfind('。'), title.rfind('.'), title.rfind('？'), title.rfind('?'), title.rfind('！'), title.rfind('!'))
                        if last_punct > 10:
                            title = title[:last_punct]
                        else:
                            title = title + "..."
                
                await save_chat_history(session_id, full_messages, title)

                yield f"data: {json.dumps({'type': 'answer', 'content': result, 'session_id': session_id})}\n\n"

            except Exception as e:
                logger.error("Failed to save chat history for %s: %s", session_id, e)
                # Still yield the answer even if saving fails
                yield f"data: {json.dumps({'type': 'answer', 'content': result, 'session_id': session_id})}\n\n"

            yield "data: [DONE]\n\n"

        except asyncio.CancelledError:
            logger.info("Task cancelled by client disconnect: %s", session_id)
            task.cancel()
            raise
        finally:
            if not task.done():
                logger.info("Cleaning up running task: %s", session_id)
                task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class DeleteMessageRequest(BaseModel):
    session_id: str
    message_index: int  # 0-based


@router.delete("/api/chat/message")
async def delete_message_endpoint(request: DeleteMessageRequest):
    """Delete a single message from a chat session by index."""
    ok = await delete_message(request.session_id, request.message_index)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "ok"}
