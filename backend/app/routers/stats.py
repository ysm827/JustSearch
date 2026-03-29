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
}

_httpx_client: httpx.AsyncClient | None = None


def set_httpx_client(client: httpx.AsyncClient | None):
    """Called from main.py lifespan to share the httpx client."""
    global _httpx_client
    _httpx_client = client


@router.get("/api/stats/github")
async def get_github_stats():
    now = datetime.now()
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


@router.get("/api/engines")
async def get_engines():
    """Return list of available search engines."""
    from ..search_engine import load_selectors, _config_cache
    return {"engines": list(_config_cache.keys()) if _config_cache else ["duckduckgo"]}


@router.get("/api/health")
async def health_check():
    pool_status = get_context_pool_status()
    from ..engine_health import engine_health
    from ..database import _engine
    
    # Memory usage info
    mem_mb = 0
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
