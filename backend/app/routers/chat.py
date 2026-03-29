"""
Chat router – /api/chat and /ws/browser/{session_id}
"""

import asyncio
import json
import logging
import os
import base64
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..database import (
    load_settings, save_chat_history, load_chat_history, get_chat_path, get_next_api_key,
    delete_message,
)
from ..workflow import SearchWorkflow
from ..browser_manager import get_interaction_session, mark_interaction_completed
from ..rate_limiter import chat_limiter
from ..logging_utils import set_request_id

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    search_engine: Optional[str] = None
    max_results: Optional[int] = 8
    max_iterations: Optional[int] = 5
    interactive_search: Optional[bool] = True


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws/browser/{session_id}")
async def browser_control_endpoint(websocket: WebSocket, session_id: str):
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
async def chat_endpoint(request: ChatRequest):
    # Set request ID for log correlation
    import uuid
    set_request_id(uuid.uuid4().hex[:8])

    # Rate limiting
    allowed, retry_after = chat_limiter.check("global")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请在 {retry_after} 秒后重试。",
        )

    defaults = await load_settings()
    api_key = defaults.get("api_key", "")

    if api_key:
        api_key = await get_next_api_key(api_key)

    base_url = request.base_url or defaults.get("base_url")

    model = request.model
    if not model:
        default_model = defaults.get("model_id", "")
        if isinstance(default_model, str) and "," in default_model:
            model = default_model.split(",")[0].strip()
        else:
            model = default_model

    search_engine = request.search_engine or defaults.get("search_engine", "duckduckgo")
    max_results = request.max_results or defaults.get("max_results", 8)
    max_iterations = request.max_iterations or defaults.get("max_iterations", 5)
    interactive_search = (
        request.interactive_search
        if request.interactive_search is not None
        else defaults.get("interactive_search", True)
    )
    max_context_turns = defaults.get("max_context_turns", 6)

    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="请先在设置中配置 API 密钥。点击左上角 ⚙️ 设置按钮，填入 API Key 后保存。",
            )

    session_id = request.session_id
    if not session_id:
        session_id = datetime.now().strftime("%Y%m%d%H%M%S")

    try:
        workflow = SearchWorkflow(
            api_key, base_url, model, search_engine, max_results,
            max_iterations, interactive_search,
            session_id=session_id, max_context_turns=max_context_turns,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    chat_path = get_chat_path(session_id)
    chat_history_data = await load_chat_history(chat_path)
    context_messages = chat_history_data.get("messages", []) if chat_history_data else []

    async def event_generator():
        yield f"data: {json.dumps({'type': 'meta', 'session_id': session_id, 'model': model})}\n\n"

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
            accumulated_sources.extend(sources)
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
