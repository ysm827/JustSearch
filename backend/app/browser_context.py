import asyncio
import logging
import os
import random
import time
import json
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Global browser state
_GLOBAL_PLAYWRIGHT = None
_GLOBAL_CONTEXT = None
_CURRENT_HEADLESS_MODE = True
_SEARCH_LOCK = asyncio.Lock()
_LAST_REQUEST_TIME = 0
_MIN_SEARCH_INTERVAL = 4.0  # Minimum seconds between search requests

# List of modern Chrome User Agents for macOS/Windows to rotate
CHROME_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.120 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
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
        await _GLOBAL_CONTEXT.close()
        _GLOBAL_CONTEXT = None
    if _GLOBAL_PLAYWRIGHT:
        await _GLOBAL_PLAYWRIGHT.stop()
        _GLOBAL_PLAYWRIGHT = None
    logger.info("Global Browser Shutdown.")


async def get_new_page() -> Page:
    """Creates a new page, restarting the browser if the context is closed."""
    global _GLOBAL_CONTEXT

    if not _GLOBAL_CONTEXT:
        await init_global_browser()

    try:
        return await _GLOBAL_CONTEXT.new_page()
    except Exception as e:
        if "Target page, context or browser has been closed" in str(e):
            logger.warning("Browser context error: %s. Restarting browser...", e)
            await shutdown_global_browser()
            # Restore previous headless mode
            await init_global_browser(headless_override=_CURRENT_HEADLESS_MODE)
            return await _GLOBAL_CONTEXT.new_page()
        raise e
