"""
Stats router – /api/stats/github and /api/health
"""

import logging
import httpx
import time
from datetime import datetime

from fastapi import APIRouter

from ..browser_context import get_context_pool_status
from ..version import __version__

logger = logging.getLogger(__name__)

# Process start time for uptime tracking
_START_TIME = time.monotonic()

router = APIRouter()

# ---------------------------------------------------------------------------
# GitHub stars cache
# ---------------------------------------------------------------------------
github_stats_cache = {
    "stars": 0,
    "last_updated": None,
    "last_error_at": None,
    "error": "",
}
GITHUB_STATS_CACHE_TTL_SECONDS = 600
GITHUB_STATS_ERROR_CACHE_TTL_SECONDS = 60
GITHUB_REPO_API_URL = "https://api.github.com/repos/yeahhe365/JustSearch"

_httpx_client: httpx.AsyncClient | None = None


def set_httpx_client(client: httpx.AsyncClient | None):
    """Called from main.py lifespan to share the httpx client."""
    global _httpx_client
    _httpx_client = client


def _cache_github_success(stars: int, fetched_at: datetime) -> dict:
    github_stats_cache["stars"] = stars
    github_stats_cache["last_updated"] = fetched_at
    github_stats_cache["last_error_at"] = None
    github_stats_cache["error"] = ""
    return {"stars": stars}


def _cache_github_error(error: str, failed_at: datetime) -> dict:
    github_stats_cache["last_error_at"] = failed_at
    github_stats_cache["error"] = error
    return {"stars": github_stats_cache["stars"], "error": error}


async def _fetch_github_repo_stats():
    if _httpx_client:
        return await _httpx_client.get(GITHUB_REPO_API_URL)
    async with httpx.AsyncClient() as client:
        return await client.get(GITHUB_REPO_API_URL)


@router.get("/api/stats/github")
async def get_github_stats():
    now = datetime.now()
    if (
        github_stats_cache["last_updated"]
        and (now - github_stats_cache["last_updated"]).total_seconds() < GITHUB_STATS_CACHE_TTL_SECONDS
    ):
        return {"stars": github_stats_cache["stars"]}

    if (
        github_stats_cache["last_error_at"]
        and (now - github_stats_cache["last_error_at"]).total_seconds() < GITHUB_STATS_ERROR_CACHE_TTL_SECONDS
    ):
        return {"stars": github_stats_cache["stars"], "error": github_stats_cache.get("error") or "Failed to fetch from GitHub"}

    try:
        response = await _fetch_github_repo_stats()
        if response.status_code == 200:
            data = response.json()
            return _cache_github_success(data.get("stargazers_count", 0), now)
        return _cache_github_error("Failed to fetch from GitHub", now)
    except Exception as e:
        return _cache_github_error(str(e), now)


@router.get("/api/engines")
async def get_engines():
    """Return list of available search engines."""
    from ..search_engine import get_all_engines
    return {"engines": get_all_engines()}


@router.get("/api/health")
async def health_check():
    pool_status = get_context_pool_status()
    from ..engine_health import engine_health
    from ..database import _engine
    
    # Memory usage info
    mem_mb = 0
    db_size_mb = 0
    try:
        import psutil
        import os
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = round(mem_info.rss / 1024 / 1024, 1)
    except ImportError:
        pass
    except Exception:
        pass
    
    # Database file size
    try:
        from ..database import _DB_PATH
        import os as _os
        if _os.path.exists(_DB_PATH):
            db_size_mb = round(_os.path.getsize(_DB_PATH) / 1024 / 1024, 2)
    except Exception:
        pass
    
    # Uptime calculation
    uptime_seconds = int(time.monotonic() - _START_TIME) if _START_TIME else 0

    return {
        "status": "ok",
        "version": __version__,
        "browser": pool_status["active_contexts"] > 0,
        "pool": pool_status,
        "engines": engine_health.get_stats(),
        "db_pool_size": _engine.pool.size() if _engine and hasattr(_engine, 'pool') else 0,
        "db_pool_checked_out": _engine.pool.checkedout() if _engine and hasattr(_engine, 'pool') else 0,
        "memory_mb": mem_mb,
        "uptime_seconds": uptime_seconds,
        "timestamp": datetime.now().isoformat(),
    }
