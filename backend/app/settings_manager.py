import asyncio
import json
import logging
import os
import secrets
import aiofiles

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'settings.json')
AUTH_TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.auth_token')

logger = logging.getLogger(__name__)

def get_or_create_auth_token() -> str:
    """Load existing auth token or create a new one and save it."""
    if os.path.exists(AUTH_TOKEN_FILE):
        try:
            with open(AUTH_TOKEN_FILE, 'r') as f:
                token = f.read().strip()
                if token:
                    return token
        except OSError as e:
            logger.warning("Failed to read auth token file: %s", e)

    # Generate new token
    token = secrets.token_urlsafe(32)
    try:
        # 使用 os.open 确保文件创建时就只有 owner 可读写 (0o600)
        fd = os.open(AUTH_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(token)
    except OSError as e:
        logger.error("Failed to save auth token: %s", e)

    return token

DEFAULT_SETTINGS = {
    "theme": "light",
    "api_key": "",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model_id": "deepseek-ai/deepseek-v3.2",
    "search_engine": "duckduckgo",
    "max_results": 8,
    "max_iterations": 5,
    "interactive_search": True,
    "max_concurrent_pages": 10,
    "max_context_turns": 6,
}

_api_key_index = 0
_api_key_lock = asyncio.Lock()

def mask_api_key(api_key: str) -> str:
    """Mask API key for display, e.g. sk-****1234"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return api_key[:3] + "****" + api_key[-4:]

async def get_next_api_key(api_keys_str: str) -> str:
    """
    Get the next API key from a comma-separated string in a round-robin fashion.
    Thread-safe with asyncio.Lock.
    """
    global _api_key_index
    if not api_keys_str:
        return api_keys_str

    keys = [k.strip() for k in api_keys_str.split(',') if k.strip()]

    if not keys:
        return ""

    if len(keys) == 1:
        return keys[0]

    async with _api_key_lock:
        current_key = keys[_api_key_index % len(keys)]
        _api_key_index = (_api_key_index + 1) % len(keys)

    return current_key

async def load_settings():
    """Load settings from the JSON file asynchronously, or return defaults if not found."""
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()
    
    try:
        async with aiofiles.open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            user_settings = json.loads(content)
            # Merge with defaults to ensure all keys exist
            settings = DEFAULT_SETTINGS.copy()
            settings.update(user_settings)
            return settings
    except Exception as e:
        logger.error("Error loading settings: %s", e)
        return DEFAULT_SETTINGS.copy()

async def save_settings(settings):
    """Save settings to the JSON file asynchronously."""
    try:
        async with aiofiles.open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(settings, indent=4, ensure_ascii=False))
        # Restrict file permissions to owner only (contains API keys)
        os.chmod(SETTINGS_FILE, 0o600)
        return True
    except Exception as e:
        logger.error("Error saving settings: %s", e)
        return False