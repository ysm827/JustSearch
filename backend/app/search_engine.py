import logging
import os
import json

logger = logging.getLogger(__name__)


def load_selectors(engine: str = "duckduckgo") -> dict:
    """Load search engine CSS selectors from config file.
    返回完整的引擎配置 dict，调用方按 engine 名取子项。
    """
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'search_selectors.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if engine in config:
                    return config
                # 引擎不在配置中，返回完整 config（调用方会 fallback 到 duckduckgo）
                return config
    except OSError as e:
        logger.error("Error loading search selectors: %s", e)

    # Fallback default
    return {
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
