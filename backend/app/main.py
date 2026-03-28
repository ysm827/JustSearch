import os
import json
import asyncio
import logging
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Body, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .workflow import SearchWorkflow
from .chat_manager import list_chats, load_chat_history, save_chat_history, delete_chat, get_chat_path, delete_all_chats
from .settings_manager import load_settings, save_settings, DEFAULT_SETTINGS, get_next_api_key, mask_api_key, SETTINGS_FILE
from .browser_manager import init_global_browser, shutdown_global_browser, get_interaction_session, mark_interaction_completed
from .browser_context import _GLOBAL_CONTEXT
import base64

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _httpx_client
    # Startup
    # 配置全局日志格式
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Startup: Loaded app from %s", __file__)
    # 复用 httpx client
    _httpx_client = httpx.AsyncClient()
    await init_global_browser()
    logger.debug("Registered routes:")
    for route in app.routes:
        if hasattr(route, "path"):
            logger.debug("  %s", route.path)
    
    yield
    
    # Shutdown
    if _httpx_client:
        await _httpx_client.aclose()
        _httpx_client = None
    await shutdown_global_browser()

app = FastAPI(title="JustSearch", lifespan=lifespan)

@app.get("/api/health")
async def health_check():
    """Lightweight health check (no auth required)."""
    return {"status": "ok", "browser": _GLOBAL_CONTEXT is not None}

# CORS - configurable via CORS_ORIGINS env var (comma-separated), defaults to localhost only
_cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static Files
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

# Models
class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    search_engine: Optional[str] = None
    max_results: Optional[int] = 8
    max_iterations: Optional[int] = 5
    interactive_search: Optional[bool] = True

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

# Sensitive fields that should be masked when sent to frontend
_SENSITIVE_FIELDS = {"api_key"}

# Endpoints

# GitHub Stats Cache
github_stats_cache = {
    "stars": 0,
    "last_updated": None
}

# 复用的 httpx 客户端，在 lifespan 中创建和关闭
_httpx_client: httpx.AsyncClient | None = None

@app.get("/api/stats/github")
async def get_github_stats():
    now = datetime.now()
    # Cache for 10 minutes
    if github_stats_cache["last_updated"] and (now - github_stats_cache["last_updated"]).total_seconds() < 600:
        return {"stars": github_stats_cache["stars"]}
    
    try:
        if _httpx_client:
            response = await _httpx_client.get("https://api.github.com/repos/yeahhe365/JustSearch")
            if response.status_code == 200:
                data = response.json()
                stars = data.get("stargazers_count", 0)
                github_stats_cache["stars"] = stars
                github_stats_cache["last_updated"] = now
                return {"stars": stars}
            else:
                return {"stars": github_stats_cache["stars"], "error": "Failed to fetch from GitHub"}
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.github.com/repos/yeahhe365/JustSearch")
                if response.status_code == 200:
                    data = response.json()
                    stars = data.get("stargazers_count", 0)
                    github_stats_cache["stars"] = stars
                    github_stats_cache["last_updated"] = now
                    return {"stars": stars}
                else:
                    return {"stars": github_stats_cache["stars"], "error": "Failed to fetch from GitHub"}
    except Exception as e:
        return {"stars": github_stats_cache["stars"], "error": str(e)}

@app.get("/api/history")
async def get_history_endpoint():
    return await list_chats()

@app.get("/api/history/{session_id}")
async def get_chat_endpoint(session_id: str):
    path = get_chat_path(session_id)
    if not os.path.exists(path):
         raise HTTPException(status_code=404, detail="Chat not found")
    history = await load_chat_history(path)
    if not history:
        # It might exist but be empty or failed to load
        return {"messages": []}
    return history

@app.delete("/api/history/{session_id}")
async def delete_chat_endpoint(session_id: str):
    await delete_chat(session_id)
    return {"status": "ok"}

@app.patch("/api/history/{session_id}")
async def rename_chat_endpoint(session_id: str, body: dict = Body(...)):
    new_title = body.get("title", "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    path = get_chat_path(session_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chat not found")
    history_data = await load_chat_history(path)
    if not history_data:
        raise HTTPException(status_code=404, detail="Chat not found")
    await save_chat_history(session_id, history_data.get("messages", []), title=new_title)
    return {"status": "ok", "title": new_title}

@app.delete("/api/history")
async def delete_all_chats_endpoint():
    await delete_all_chats()
    return {"status": "ok"}

@app.post("/api/clear-cache")
async def clear_cache_endpoint():
    """清除所有缓存：聊天记录 + 浏览器数据 + 重置设置。"""
    import shutil
    import glob as glob_mod

    # 1. 删除所有聊天记录
    await delete_all_chats()

    # 2. 删除浏览器持久化数据 (cookies, 配置等)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    user_data_dir = os.path.join(project_root, "user_data")
    if os.path.exists(user_data_dir):
        shutil.rmtree(user_data_dir, ignore_errors=True)
        os.makedirs(user_data_dir, exist_ok=True)

    # 3. 重置设置为默认值
    if os.path.exists(SETTINGS_FILE):
        os.remove(SETTINGS_FILE)

    return {"status": "ok"}

@app.get("/api/settings")
async def get_settings_endpoint():
    settings = await load_settings()
    # Mask sensitive fields before sending to frontend
    for field in _SENSITIVE_FIELDS:
        if field in settings and settings[field]:
            settings[field] = mask_api_key(settings[field])
    return settings

@app.get("/api/settings/default")
def get_default_settings_endpoint():
    settings = DEFAULT_SETTINGS.copy()
    for field in _SENSITIVE_FIELDS:
        if field in settings and settings[field]:
            settings[field] = mask_api_key(settings[field])
    return settings

@app.post("/api/settings")
async def update_settings_endpoint(settings: SettingsModel):
    # Convert pydantic model to dict, excluding None values if needed,
    # but here we want to overwrite so we use model_dump
    current = await load_settings()
    new_settings = settings.model_dump(exclude_none=True)

    # Skip empty string values (Pydantic defaults) so partial updates don't wipe existing data.
    # Only api_key has special handling: masked values (****) preserve the real key.
    update = {}
    for k, v in new_settings.items():
        if v == "":
            continue
        update[k] = v

    # Handle masked api_key (starts with prefix + ****)
    incoming_key = new_settings.get("api_key", "")
    if incoming_key and "****" in incoming_key:
        update["api_key"] = current.get("api_key", "")

    # Merge with current to preserve keys not in model if any
    current.update(update)

    if await save_settings(current):
        # Return masked settings
        for field in _SENSITIVE_FIELDS:
            if field in current and current[field]:
                current[field] = mask_api_key(current[field])
        return {"status": "ok", "settings": current}
    raise HTTPException(status_code=500, detail="Failed to save settings")

@app.websocket("/ws/browser/{session_id}")
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
                    # Capture screenshot
                    # quality=50 is a good balance for speed
                    screenshot = await page.screenshot(type="jpeg", quality=50)
                    b64_img = base64.b64encode(screenshot).decode("utf-8")
                    await websocket.send_json({
                        "type": "frame",
                        "image": b64_img
                    })
                except Exception as e:
                    logger.error("Frame error: %s", e)
                    # If page is closed, we should stop
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
                    # User signaled completion
                    await mark_interaction_completed(session_id)
                    await websocket.send_json({"type": "status", "msg": "Completed"})
                    break
                    
        except Exception as e:
            logger.error("Input error: %s", e)

    # Run both
    tasks = [
        asyncio.create_task(send_frames()),
        asyncio.create_task(receive_events())
    ]
    
    try:
        # Wait until one finishes (usually receive_events on close or complete)
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

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    defaults = await load_settings()
    api_key = defaults.get("api_key", "")

    # Apply round-robin selection if multiple keys are provided
    if api_key:
        api_key = await get_next_api_key(api_key)

    base_url = request.base_url or defaults.get("base_url")
    
    model = request.model
    if not model:
        default_model = defaults.get("model_id", "")
        if "," in default_model:
            model = default_model.split(",")[0].strip()
        else:
            model = default_model
            
    search_engine = request.search_engine or defaults.get("search_engine", "duckduckgo")
    max_results = request.max_results or defaults.get("max_results", 8)
    max_iterations = request.max_iterations or defaults.get("max_iterations", 5)
    interactive_search = request.interactive_search if request.interactive_search is not None else defaults.get("interactive_search", True)
    max_context_turns = defaults.get("max_context_turns", 6)
    
    if not api_key:
        # Fallback to env var if available
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="请先在设置中配置 API 密钥（API Key）。"
            )

    # Ensure session_id
    session_id = request.session_id
    if not session_id:
        session_id = datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Initialize Workflow
    # Note: SearchWorkflow might fail if api_key is missing. 
    # We should catch this.
    try:
        workflow = SearchWorkflow(api_key, base_url, model, search_engine, max_results, max_iterations, interactive_search, session_id=session_id, max_context_turns=max_context_turns)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Load existing history for context
    chat_path = get_chat_path(session_id)
    chat_history_data = await load_chat_history(chat_path)
    context_messages = chat_history_data.get("messages", []) if chat_history_data else []
    
    async def event_generator():
        # Send session_id immediately
        yield f"data: {json.dumps({'type': 'meta', 'session_id': session_id})}\n\n"

        queue = asyncio.Queue()
        logs = []
        accumulated_sources = []
        
        def progress_callback(msg):
            logs.append(msg)
            queue.put_nowait({"type": "log", "content": msg})

        def stream_callback(chunk):
            queue.put_nowait({"type": "answer_chunk", "content": chunk})
            
        def source_callback(sources):
            accumulated_sources.extend(sources)
            queue.put_nowait({"type": "sources", "content": sources})

        task = asyncio.create_task(workflow.run(request.query, progress_callback, stream_callback, context_messages, source_callback))
        
        try:
            while not task.done():
                try:
                    # Use a shorter timeout for faster SSE delivery
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(item)}\n\n"
                    
                    # Flush any additional items immediately (batch send)
                    while not queue.empty():
                        try:
                            extra = queue.get_nowait()
                            yield f"data: {json.dumps(extra)}\n\n"
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    # Send a lightweight keepalive to ensure the connection stays alive
                    # and the frontend knows the backend is still working
                    yield f": keepalive\n\n"
                    continue
                    
            # Check for exception in task
            if task.exception():
                 yield f"data: {json.dumps({'type': 'error', 'content': str(task.exception())})}\n\n"
                 return

            # Flush remaining items in queue
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.QueueEmpty:
                    break
                    
            result = task.result()
            
            # Save History
            try:
                # Load existing history first to append
                path = get_chat_path(session_id)
                existing_data = await load_chat_history(path)
                existing_messages = existing_data.get("messages", []) if existing_data else []
                
                new_messages = [
                    {"role": "user", "content": request.query},
                    {"role": "assistant", "content": result, "logs": logs, "sources": accumulated_sources}
                ]
                
                full_messages = existing_messages + new_messages
                
                # Use the existing title or generate new one
                title = existing_data.get("title") if existing_data else None
                
                await save_chat_history(session_id, full_messages, title)
                
                yield f"data: {json.dumps({'type': 'answer', 'content': result, 'session_id': session_id})}\n\n"
                
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': f'Failed to save history: {e}'})}\n\n"

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

@app.get("/")
async def read_index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")