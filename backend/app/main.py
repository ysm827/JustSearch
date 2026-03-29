import asyncio
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
from .browser_context import init_global_browser, shutdown_global_browser

from .routers import chat as chat_router
from .routers import history as history_router
from .routers import settings as settings_router
from .routers import stats as stats_router
from .version import __version__

# Background cleanup task
_cleanup_task = None


async def _periodic_cleanup():
    """Periodically clean up rate limiter and other in-memory state."""
    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            from .rate_limiter import chat_limiter
            chat_limiter.cleanup()
            # Clean expired search cache
            from .browser_manager import _search_cache, _SEARCH_CACHE_TTL
            import time
            now = time.time()
            expired = [k for k, (_, ts) in _search_cache.items() if now - ts > _SEARCH_CACHE_TTL]
            for k in expired:
                del _search_cache[k]
            if expired:
                logger.debug("Cleaned %d expired search cache entries", len(expired))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Cleanup task error: %s", e)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared httpx client (used by stats router)
# ---------------------------------------------------------------------------
_httpx_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _httpx_client

    # Startup
    from .logging_utils import setup_logging
    setup_logging(
        level=logging.DEBUG if os.getenv("DEBUG", "").lower() == "true" else logging.INFO
    )
    logger.info("Startup: Loaded app from %s", __file__)

    # Initialise database (creates tables, runs legacy migration)
    await init_db()

    _httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    stats_router.set_httpx_client(_httpx_client)

    await init_global_browser()

    # Start periodic cleanup
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_periodic_cleanup())

    logger.debug("Registered routes:")
    for route in app.routes:
        if hasattr(route, "path"):
            logger.debug("  %s", route.path)

    yield

    # Shutdown
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass

    if _httpx_client:
        await _httpx_client.aclose()
        _httpx_client = None
        stats_router.set_httpx_client(None)
    await shutdown_global_browser()


app = FastAPI(title="JustSearch", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_cors_origins_str = os.getenv("CORS_ORIGINS", "*")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
if "*" in _cors_origins:
    _cors_origins = ["*"]

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
