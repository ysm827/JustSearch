import os
import logging
import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .browser_manager import init_global_browser, shutdown_global_browser
from .browser_context import _GLOBAL_CONTEXT  # legacy compat import

from .routers import chat as chat_router
from .routers import history as history_router
from .routers import settings as settings_router
from .routers import stats as stats_router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared httpx client (used by stats router)
# ---------------------------------------------------------------------------
_httpx_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _httpx_client

    # Startup
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Startup: Loaded app from %s", __file__)

    # Initialise database (creates tables, runs legacy migration)
    await init_db()

    _httpx_client = httpx.AsyncClient()
    stats_router.set_httpx_client(_httpx_client)

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
        stats_router.set_httpx_client(None)
    await shutdown_global_browser()


app = FastAPI(title="JustSearch", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static Files
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------
app.include_router(chat_router.router)
app.include_router(history_router.router)
app.include_router(settings_router.router)
app.include_router(stats_router.router)

# ---------------------------------------------------------------------------
# Root route
# ---------------------------------------------------------------------------


@app.get("/")
async def read_index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)


@app.get("/c/{session_id}")
async def read_chat_session(session_id: str):
    """Serve the same SPA index for any chat URL — client-side routing handles the rest."""
    html_path = os.path.join(STATIC_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
