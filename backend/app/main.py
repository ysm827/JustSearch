import os
import json
import asyncio
import logging
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Body, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from .workflow import SearchWorkflow
from .chat_manager import list_chats, load_chat_history, save_chat_history, delete_chat, get_chat_path, delete_all_chats
from .settings_manager import load_settings, save_settings, DEFAULT_SETTINGS, get_next_api_key, mask_api_key, get_or_create_auth_token
from .browser_manager import init_global_browser, shutdown_global_browser, get_interaction_session, mark_interaction_completed
import base64

logger = logging.getLogger(__name__)

# Authentication - token is injected into the HTML page automatically
AUTH_TOKEN = None
_bearer_scheme = HTTPBearer(auto_error=False)

def _get_auth_token():
    """Lazy-load auth token."""
    global AUTH_TOKEN
    if AUTH_TOKEN is None:
        AUTH_TOKEN = get_or_create_auth_token()
    return AUTH_TOKEN

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> None:
    """Verify Bearer token for API endpoints."""
    token = _get_auth_token()
    if credentials is None or credentials.credentials != token:
        raise HTTPException(status_code=401, detail="Unauthorized")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    token = get_or_create_auth_token()
    logger.info("Startup: Loaded app from %s", __file__)
    logger.info("Auth token: %s", token)
    logger.info("Access the app with Authorization: Bearer %s", token)
    await init_global_browser()
    logger.debug("Registered routes:")
    for route in app.routes:
        if hasattr(route, "path"):
            logger.debug("  %s", route.path)
    
    yield
    
    # Shutdown
    await shutdown_global_browser()

app = FastAPI(title="JustSearch", lifespan=lifespan)

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

# Sensitive fields that should be masked when sent to frontend
_SENSITIVE_FIELDS = {"api_key"}

# Endpoints

# GitHub Stats Cache
github_stats_cache = {
    "stars": 0,
    "last_updated": None
}

@app.get("/api/stats/github")
async def get_github_stats(_auth: None = Depends(verify_token)):
    now = datetime.now()
    # Cache for 10 minutes
    if github_stats_cache["last_updated"] and (now - github_stats_cache["last_updated"]).total_seconds() < 600:
        return {"stars": github_stats_cache["stars"]}
    
    try:
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
async def get_history_endpoint(_auth: None = Depends(verify_token)):
    return await list_chats()

@app.get("/api/history/{session_id}")
async def get_chat_endpoint(session_id: str, _auth: None = Depends(verify_token)):
    path = get_chat_path(session_id)
    if not os.path.exists(path):
         raise HTTPException(status_code=404, detail="Chat not found")
    history = await load_chat_history(path)
    if not history:
        # It might exist but be empty or failed to load
        return {"messages": []}
    return history

@app.delete("/api/history/{session_id}")
def delete_chat_endpoint(session_id: str, _auth: None = Depends(verify_token)):
    delete_chat(session_id)
    return {"status": "ok"}

@app.delete("/api/history")
def delete_all_chats_endpoint(_auth: None = Depends(verify_token)):
    delete_all_chats()
    return {"status": "ok"}

@app.get("/api/settings")
async def get_settings_endpoint(_auth: None = Depends(verify_token)):
    settings = await load_settings()
    # Mask sensitive fields before sending to frontend
    for field in _SENSITIVE_FIELDS:
        if field in settings and settings[field]:
            settings[field] = mask_api_key(settings[field])
    return settings

@app.get("/api/settings/default")
def get_default_settings_endpoint(_auth: None = Depends(verify_token)):
    settings = DEFAULT_SETTINGS.copy()
    for field in _SENSITIVE_FIELDS:
        if field in settings and settings[field]:
            settings[field] = mask_api_key(settings[field])
    return settings

@app.post("/api/settings")
async def update_settings_endpoint(settings: SettingsModel, _auth: None = Depends(verify_token)):
    # Convert pydantic model to dict, excluding None values if needed,
    # but here we want to overwrite so we use model_dump
    current = await load_settings()
    new_settings = settings.model_dump()

    # If api_key is masked (starts with same prefix + ****), keep the existing one
    incoming_key = new_settings.get("api_key", "")
    if incoming_key and "****" in incoming_key:
        new_settings["api_key"] = current.get("api_key", "")

    # Merge with current to preserve keys not in model if any
    current.update(new_settings)

    if await save_settings(current):
        # Return masked settings
        for field in _SENSITIVE_FIELDS:
            if field in current and current[field]:
                current[field] = mask_api_key(current[field])
        return {"status": "ok", "settings": current}
    raise HTTPException(status_code=500, detail="Failed to save settings")

@app.websocket("/ws/browser/{session_id}")
async def browser_control_endpoint(websocket: WebSocket, session_id: str):
    # Verify token from query parameter
    token = websocket.query_params.get("token")
    if not token or token != _get_auth_token():
        await websocket.close(code=4001, reason="Unauthorized")
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
async def chat_endpoint(request: ChatRequest, _auth: None = Depends(verify_token)):
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
    
    if not api_key:
        # Fallback to env var if available, or error
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
             # We can't raise HTTP exception inside streaming response easily if we start streaming.
             # But here we haven't started.
             pass # Let the workflow fail or prompt user

    # Ensure session_id
    session_id = request.session_id
    if not session_id:
        session_id = datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Initialize Workflow
    # Note: SearchWorkflow might fail if api_key is missing. 
    # We should catch this.
    try:
        workflow = SearchWorkflow(api_key, base_url, model, search_engine, max_results, max_iterations, interactive_search, session_id=session_id)
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
        
        def progress_callback(msg):
            logs.append(msg)
            queue.put_nowait({"type": "log", "content": msg})

        def stream_callback(chunk):
            queue.put_nowait({"type": "answer_chunk", "content": chunk})
            
        def source_callback(sources):
            queue.put_nowait({"type": "sources", "content": sources})

        task = asyncio.create_task(workflow.run(request.query, progress_callback, stream_callback, context_messages, source_callback))
        
        try:
            while not task.done():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    continue
                    
            # Check for exception in task
            if task.exception():
                 yield f"data: {json.dumps({'type': 'error', 'content': str(task.exception())})}\n\n"
                 return

            # Flush remaining logs
            while not queue.empty():
                item = queue.get_nowait()
                yield f"data: {json.dumps(item)}\n\n"
                
            result = task.result()
            
            # Save History
            try:
                # Load existing history first to append
                path = get_chat_path(session_id)
                existing_data = await load_chat_history(path)
                existing_messages = existing_data.get("messages", []) if existing_data else []
                
                new_messages = [
                    {"role": "user", "content": request.query},
                    {"role": "assistant", "content": result, "logs": logs}
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
    # Inject auth token into the HTML so the frontend can use it automatically
    html_path = os.path.join(STATIC_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    token = _get_auth_token()
    # Insert a meta tag with the token right after <head>
    html = html.replace(
        "<head>",
        f'<head>\n<meta name="auth-token" content="{token}">',
        1
    )
    return HTMLResponse(content=html)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")