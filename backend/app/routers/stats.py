"""
Stats router – /api/stats/github and /api/health
"""

import logging
import httpx
from datetime import datetime

from fastapi import APIRouter

from ..browser_context import get_context_pool_status

logger = logging.getLogger(__name__)

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


@router.get("/api/health")
async def health_check():
    pool_status = get_context_pool_status()
    return {"status": "ok", "browser": pool_status["active_contexts"] > 0}
