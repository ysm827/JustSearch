import asyncio
import logging
import os
import random
import time
import json
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# =============================================
# 全局浏览器状态（测试时可通过 reset_state() 重置）
# =============================================
_GLOBAL_PLAYWRIGHT = None
_GLOBAL_CONTEXT = None
_CURRENT_HEADLESS_MODE = True
_SEARCH_LOCK = asyncio.Lock()
_LAST_REQUEST_TIME = 0
_MIN_SEARCH_INTERVAL = 4.0  # Minimum seconds between search requests

# 并发控制：限制同时打开的页面数，防止 OOM
_MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "10"))
_PAGE_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)
_MAX_BROWSER_RESTART_RETRIES = 3  # 浏览器重启最大重试次数

# List of modern Chrome User Agents for macOS/Windows to rotate
CHROME_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]


def get_browser_config(user_data_dir: str) -> dict:
    """Get or create persistent browser configuration (UA, viewport)"""
    config_path = os.path.join(user_data_dir, "browser_config.json")
    config = {}

    # Try to load existing config
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except OSError as e:
            logger.error("Error loading browser config: %s", e)

    # Generate missing fields
    if "user_agent" not in config:
        config["user_agent"] = random.choice(CHROME_USER_AGENTS)

    if "viewport" not in config:
        config["viewport"] = {
            "width": 1280 + random.randint(0, 100),
            "height": 720 + random.randint(0, 100)
        }

    # Save config back
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except OSError as e:
        logger.error("Error saving browser config: %s", e)

    return config


async def init_global_browser(headless_override: bool = None):
    """Initializes the global browser instance."""
    global _GLOBAL_PLAYWRIGHT, _GLOBAL_CONTEXT, _CURRENT_HEADLESS_MODE

    if _GLOBAL_CONTEXT:
        return

    from playwright.async_api import async_playwright

    _GLOBAL_PLAYWRIGHT = await async_playwright().start()

    # Calculate project root from current file: backend/app/browser_context.py -> ... -> root
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    user_data_dir = os.path.join(project_root, "user_data")
    if not os.path.exists(user_data_dir):
        os.makedirs(user_data_dir)

    # Get persistent browser config
    browser_config = get_browser_config(user_data_dir)
    user_agent = browser_config["user_agent"]
    width = browser_config["viewport"]["width"]
    height = browser_config["viewport"]["height"]

    # Determine headless mode from environment variable (default: True)
    if headless_override is not None:
        headless_mode = headless_override
    else:
        headless_mode = os.getenv("HEADLESS", "true").lower() == "true"

    _CURRENT_HEADLESS_MODE = headless_mode

    try:
        _GLOBAL_CONTEXT = await _GLOBAL_PLAYWRIGHT.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",  # Use real Chrome if available
            headless=headless_mode,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-extensions',
                '--window-size=1920,1080',
            ],
            viewport={"width": width, "height": height},
            user_agent=user_agent,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            ignore_default_args=["--enable-automation"],
        )
    except Exception as e:
        logger.warning("Failed to launch Chrome: %s. Trying default Chromium...", e)
        _GLOBAL_CONTEXT = await _GLOBAL_PLAYWRIGHT.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless_mode,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox',
                '--window-size=1920,1080',
            ],
            viewport={"width": width, "height": height},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=user_agent,
            ignore_default_args=["--enable-automation"]
        )
    logger.info("Global Browser Initialized with UA: %s", user_agent)


async def shutdown_global_browser():
    """Shuts down the global browser instance."""
    global _GLOBAL_PLAYWRIGHT, _GLOBAL_CONTEXT
    if _GLOBAL_CONTEXT:
        try:
            await _GLOBAL_CONTEXT.close()
        except Exception as e:
            logger.warning("Error closing browser context: %s", e)
        _GLOBAL_CONTEXT = None
    if _GLOBAL_PLAYWRIGHT:
        try:
            await _GLOBAL_PLAYWRIGHT.stop()
        except Exception as e:
            logger.warning("Error stopping playwright: %s", e)
        _GLOBAL_PLAYWRIGHT = None
    logger.info("Global Browser Shutdown.")


async def get_new_page() -> Page:
    """
    Creates a new page with concurrency control.
    Restarts the browser if the context is closed (up to _MAX_BROWSER_RESTART_RETRIES times).
    """
    global _GLOBAL_CONTEXT

    if not _GLOBAL_CONTEXT:
        await init_global_browser()

    # 并发控制：等待信号量
    await _PAGE_SEMAPHORE.acquire()

    for attempt in range(1, _MAX_BROWSER_RESTART_RETRIES + 1):
        try:
            return await _GLOBAL_CONTEXT.new_page()
        except Exception as e:
            if "Target page, context or browser has been closed" in str(e):
                logger.warning("Browser context error (attempt %d/%d): %s",
                               attempt, _MAX_BROWSER_RESTART_RETRIES, e)
                try:
                    await shutdown_global_browser()
                    await init_global_browser(headless_override=_CURRENT_HEADLESS_MODE)
                except Exception as restart_err:
                    logger.error("Browser restart failed (attempt %d/%d): %s",
                                 attempt, _MAX_BROWSER_RESTART_RETRIES, restart_err)
                    if attempt == _MAX_BROWSER_RESTART_RETRIES:
                        _PAGE_SEMAPHORE.release()
                        raise RuntimeError(
                            f"浏览器连续重启 {_MAX_BROWSER_RESTART_RETRIES} 次均失败: {restart_err}"
                        ) from restart_err
                    await asyncio.sleep(2 * attempt)  # 指数退避
            else:
                _PAGE_SEMAPHORE.release()
                raise e

    # 理论上不会到这里
    _PAGE_SEMAPHORE.release()
    raise RuntimeError("无法创建新的浏览器页面")


async def release_page(page: Page):
    """关闭页面并释放信号量。应在 finally 块中调用。"""
    try:
        if page:
            await page.close()
    except Exception as e:
        logger.warning("关闭页面时出错: %s", e)
    finally:
        _PAGE_SEMAPHORE.release()


def reset_state():
    """重置所有全局状态，仅用于测试。"""
    global _GLOBAL_PLAYWRIGHT, _GLOBAL_CONTEXT, _CURRENT_HEADLESS_MODE
    global _SEARCH_LOCK, _LAST_REQUEST_TIME, _PAGE_SEMAPHORE
    _GLOBAL_PLAYWRIGHT = None
    _GLOBAL_CONTEXT = None
    _CURRENT_HEADLESS_MODE = True
    _SEARCH_LOCK = asyncio.Lock()
    _LAST_REQUEST_TIME = 0
    _PAGE_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)
