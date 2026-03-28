"""
Browser context pool with auto-rotation.

Maintains a pool of browser contexts (default 2), tracks request counts and
age per context, and automatically rotates stale contexts in the background.
"""

import asyncio
import logging
import os
import random
import time
import json
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pool configuration
# ---------------------------------------------------------------------------
_POOL_SIZE = 2
_MAX_REQUESTS_PER_CONTEXT = 50
_MAX_CONTEXT_AGE_SECONDS = 3600  # 1 hour
_ROTATION_CHECK_INTERVAL = 60  # seconds between background checks
_MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "10"))
_MAX_BROWSER_RESTART_RETRIES = 3


@dataclass
class ContextSlot:
    context: object = None  # BrowserContext
    created_at: float = 0.0
    request_count: int = 0


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_GLOBAL_PLAYWRIGHT = None
_CURRENT_HEADLESS_MODE = True
_SEARCH_LOCK = asyncio.Lock()
_LAST_REQUEST_TIME = 0
_MIN_SEARCH_INTERVAL = 4.0

_PAGE_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

# The pool – list of ContextSlot
_context_pool: list[ContextSlot] = []
_pool_lock = asyncio.Lock()
_rotation_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# User-agent rotation
# ---------------------------------------------------------------------------
CHROME_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]


def get_browser_config(user_data_dir: str) -> dict:
    config_path = os.path.join(user_data_dir, "browser_config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except OSError as e:
            logger.error("Error loading browser config: %s", e)

    if "user_agent" not in config:
        config["user_agent"] = random.choice(CHROME_USER_AGENTS)

    if "viewport" not in config:
        config["viewport"] = {
            "width": 1280 + random.randint(0, 100),
            "height": 720 + random.randint(0, 100),
        }

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError as e:
        logger.error("Error saving browser config: %s", e)

    return config


# ---------------------------------------------------------------------------
# Context lifecycle helpers
# ---------------------------------------------------------------------------


async def _create_context(headless_override: bool = None) -> object:
    """Launch a single persistent browser context and return it."""
    from playwright.async_api import async_playwright

    global _GLOBAL_PLAYWRIGHT

    if _GLOBAL_PLAYWRIGHT is None:
        _GLOBAL_PLAYWRIGHT = await async_playwright().start()

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    # Use a unique user_data_dir per context so they don't conflict
    ctx_index = len(_context_pool)
    user_data_dir = os.path.join(project_root, "user_data", f"ctx_{ctx_index}")
    os.makedirs(user_data_dir, exist_ok=True)

    browser_config = get_browser_config(user_data_dir)
    user_agent = browser_config["user_agent"]
    width = browser_config["viewport"]["width"]
    height = browser_config["viewport"]["height"]

    if headless_override is not None:
        headless_mode = headless_override
    else:
        headless_mode = os.getenv("HEADLESS", "true").lower() == "true"

    global _CURRENT_HEADLESS_MODE
    _CURRENT_HEADLESS_MODE = headless_mode

    launch_kwargs = dict(
        user_data_dir=user_data_dir,
        headless=headless_mode,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-extensions",
            "--window-size=1920,1080",
        ],
        viewport={"width": width, "height": height},
        user_agent=user_agent,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        ignore_default_args=["--enable-automation"],
    )

    # Try Chrome first, fall back to Chromium
    try:
        ctx = await _GLOBAL_PLAYWRIGHT.chromium.launch_persistent_context(
            channel="chrome", **launch_kwargs,
        )
    except Exception as e:
        logger.warning("Failed to launch Chrome: %s. Trying default Chromium...", e)
        launch_kwargs.pop("channel", None)  # channel kwarg already consumed
        ctx = await _GLOBAL_PLAYWRIGHT.chromium.launch_persistent_context(**launch_kwargs)

    logger.info("Created browser context (UA: %s)", user_agent)
    return ctx


async def _init_pool(headless_override: bool = None):
    """Fill the pool up to _POOL_SIZE."""
    async with _pool_lock:
        for _ in range(_POOL_SIZE - len(_context_pool)):
            ctx = await _create_context(headless_override)
            _context_pool.append(ContextSlot(context=ctx, created_at=time.time(), request_count=0))
    logger.info("Browser context pool initialised (%d contexts)", len(_context_pool))


async def _rotate_stale_contexts():
    """Check every slot and rotate contexts that exceeded limits."""
    now = time.time()
    async with _pool_lock:
        for i, slot in enumerate(_context_pool):
            if slot.context is None:
                continue
            stale = (
                slot.request_count >= _MAX_REQUESTS_PER_CONTEXT
                or (now - slot.created_at) >= _MAX_CONTEXT_AGE_SECONDS
            )
            if not stale:
                continue

            logger.info(
                "Rotating context %d (requests=%d, age=%.0fs)",
                i, slot.request_count, now - slot.created_at,
            )
            try:
                await slot.context.close()
            except Exception as e:
                logger.warning("Error closing stale context %d: %s", i, e)

            try:
                new_ctx = await _create_context(_CURRENT_HEADLESS_MODE)
                _context_pool[i] = ContextSlot(context=new_ctx, created_at=time.time(), request_count=0)
            except Exception as e:
                logger.error("Failed to create replacement context %d: %s", i, e)
                _context_pool[i] = ContextSlot(context=None, created_at=0, request_count=0)


async def _rotation_loop():
    """Background task that periodically checks and rotates stale contexts."""
    while True:
        try:
            await asyncio.sleep(_ROTATION_CHECK_INTERVAL)
            await _rotate_stale_contexts()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Rotation loop error: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def init_global_browser(headless_override: bool = None):
    """Initialise the context pool (replaces old single-context init)."""
    await _init_pool(headless_override)
    global _rotation_task
    _rotation_task = asyncio.create_task(_rotation_loop())


async def shutdown_global_browser():
    """Close all contexts and stop playwright."""
    global _GLOBAL_PLAYWRIGHT, _rotation_task

    if _rotation_task:
        _rotation_task.cancel()
        try:
            await _rotation_task
        except asyncio.CancelledError:
            pass
        _rotation_task = None

    async with _pool_lock:
        for slot in _context_pool:
            if slot.context:
                try:
                    await slot.context.close()
                except Exception as e:
                    logger.warning("Error closing context: %s", e)
        _context_pool.clear()

    if _GLOBAL_PLAYWRIGHT:
        try:
            await _GLOBAL_PLAYWRIGHT.stop()
        except Exception as e:
            logger.warning("Error stopping playwright: %s", e)
        _GLOBAL_PLAYWRIGHT = None

    logger.info("Global Browser Shutdown.")


def _pick_slot_index() -> int:
    """Pick the slot with the fewest requests."""
    if not _context_pool:
        raise RuntimeError("Context pool is empty")
    best = 0
    for i in range(1, len(_context_pool)):
        if _context_pool[i].request_count < _context_pool[best].request_count:
            best = i
    return best


async def get_new_page() -> Page:
    """Get a new page from the least-loaded context in the pool."""
    # Ensure pool is initialised
    if not _context_pool or all(s.context is None for s in _context_pool):
        await init_global_browser()

    await _PAGE_SEMAPHORE.acquire()

    for attempt in range(1, _MAX_BROWSER_RESTART_RETRIES + 1):
        async with _pool_lock:
            idx = _pick_slot_index()
            slot = _context_pool[idx]

            if slot.context is None:
                # Slot is empty, try to fill it
                try:
                    slot.context = await _create_context(_CURRENT_HEADLESS_MODE)
                    slot.created_at = time.time()
                    slot.request_count = 0
                except Exception as e:
                    logger.error("Failed to create context for slot %d: %s", idx, e)
                    if attempt == _MAX_BROWSER_RESTART_RETRIES:
                        _PAGE_SEMAPHORE.release()
                        raise RuntimeError(f"Cannot create browser context after {_MAX_BROWSER_RESTART_RETRIES} attempts") from e
                    await asyncio.sleep(2 * attempt)
                    continue

            try:
                page = await slot.context.new_page()
                slot.request_count += 1
                return page
            except Exception as e:
                if "Target page, context or browser has been closed" in str(e) or "has been closed" in str(e):
                    logger.warning("Context %d closed (attempt %d/%d): %s", idx, attempt, _MAX_BROWSER_RESTART_RETRIES, e)
                    try:
                        if slot.context:
                            await slot.context.close()
                    except Exception:
                        pass
                    slot.context = None
                    if attempt == _MAX_BROWSER_RESTART_RETRIES:
                        _PAGE_SEMAPHORE.release()
                        raise RuntimeError(f"Browser restart failed {_MAX_BROWSER_RESTART_RETRIES} times: {e}") from e
                    await asyncio.sleep(2 * attempt)
                else:
                    _PAGE_SEMAPHORE.release()
                    raise e

    _PAGE_SEMAPHORE.release()
    raise RuntimeError("Unable to create a new browser page")


async def release_page(page: Page):
    """Close page and release semaphore."""
    try:
        if page:
            await page.close()
    except Exception as e:
        logger.warning("Error closing page: %s", e)
    finally:
        _PAGE_SEMAPHORE.release()


def get_context_pool_status() -> dict:
    """Return pool status for health checks."""
    active = sum(1 for s in _context_pool if s.context is not None)
    return {
        "pool_size": len(_context_pool),
        "active_contexts": active,
        "slots": [
            {
                "index": i,
                "active": s.context is not None,
                "requests": s.request_count,
                "age_seconds": int(time.time() - s.created_at) if s.created_at else 0,
            }
            for i, s in enumerate(_context_pool)
        ],
    }


# For backward compatibility – browser_manager.py references these
_GLOBAL_CONTEXT = None  # legacy compat, always None now


def reset_state():
    """Reset all global state (testing only)."""
    global _GLOBAL_PLAYWRIGHT, _GLOBAL_CONTEXT, _CURRENT_HEADLESS_MODE
    global _SEARCH_LOCK, _LAST_REQUEST_TIME, _PAGE_SEMAPHORE
    global _context_pool, _rotation_task
    _GLOBAL_PLAYWRIGHT = None
    _GLOBAL_CONTEXT = None
    _CURRENT_HEADLESS_MODE = True
    _SEARCH_LOCK = asyncio.Lock()
    _LAST_REQUEST_TIME = 0
    _PAGE_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)
    _context_pool.clear()
    if _rotation_task:
        _rotation_task.cancel()
        _rotation_task = None
