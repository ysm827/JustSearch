import logging
import os
import json
import time

logger = logging.getLogger(__name__)

# Cache for selector config with hot-reload support
_config_cache: dict = {}
_config_mtime: float = 0.0


def load_selectors(engine: str = "duckduckgo") -> dict:
    """Load search engine CSS selectors from config file.
    
    Supports hot-reload: if the config file has been modified since last load,
    it will be re-read automatically.
    
    Returns the full engine config dict. Callers extract the specific engine by name.
    """
    global _config_cache, _config_mtime

    config_path = os.path.join(os.path.dirname(__file__), 'search_selectors.json')
    
    try:
        # Check for file modification (hot-reload)
        current_mtime = os.path.getmtime(config_path)
        if current_mtime != _config_mtime:
            with open(config_path, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
                _config_mtime = current_mtime
                logger.info("[SearchEngine] 重新加载搜索引擎配置 (mtime=%.0f)", _config_mtime)
    except OSError as e:
        logger.error("[SearchEngine] 加载搜索引擎配置失败: %s", e)
    
    config = _config_cache
    if not config:
        # Fallback default
        config = {
            "duckduckgo": {
                "base_url": "https://duckduckgo.com/?q={query}",
                "selectors": {
                    "result_container": ["article[data-testid='result']", ".react-results--main li"],
                    "title": "h2",
                    "link": "a[data-testid='result-title-a']",
                    "snippet": "[data-testid='result-snippet']",
                    "date": ".result__timestamp"
                },
                "captcha_check": [],
                "wait_selector": "#react-layout, .react-results--main"
            }
        }

    if engine is None:
        return config

    if engine in config:
        return config[engine]
    # Engine not in config — return full config (caller will fallback to duckduckgo)
    return config.get("duckduckgo", {})


def get_all_engines() -> list:
    """Return a list of all available search engine names."""
    global _config_cache
    if not _config_cache:
        load_selectors()  # Force load
    return list(_config_cache.keys()) if _config_cache else ["duckduckgo"]
