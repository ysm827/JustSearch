import asyncio
from contextlib import asynccontextmanager

from backend.app import browser_manager
from backend.app.crawler import redirects
from backend.app.browser_manager import BrowserManager
from backend.app.engine_health import EngineHealthMonitor, engine_health


def test_resolve_redirect_url_extracts_sogou_script_target(monkeypatch):
    wrapper_url = "https://www.sogou.com/link?url=opaque-token"
    target_url = "https://download.csdn.net/blog/column/12812907/146013916"

    async def fake_fetch(url):
        assert url == wrapper_url
        return (
            '<meta content="always" name="referrer">'
            f'<script>window.location.replace("{target_url}")</script>'
            f"<noscript><META http-equiv=\"refresh\" content=\"0;URL='{target_url}'\"></noscript>"
        )

    monkeypatch.setattr(redirects, "_fetch_sogou_redirect_html", fake_fetch, raising=False)

    assert asyncio.run(redirects.resolve_redirect_url(wrapper_url)) == target_url


def test_fallback_text_extract_cleans_multiline_search_titles():
    class FakePage:
        async def evaluate(self, *_args):
            return [
                {
                    "title": (
                        "FastAPI\n"
                        "fastapi.tiangolo.com > tutorial > cors\n"
                        "CORS (Cross-Origin Resource Sharing) - FastAPI"
                    ),
                    "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                    "snippet": "allow_origins - A list of origins that should be permitted.",
                }
            ]

    manager = BrowserManager(engine="brave", max_results=3)
    results = asyncio.run(manager._fallback_text_extract(FakePage(), "FastAPI CORS"))

    assert results[0]["title"] == "CORS (Cross-Origin Resource Sharing) - FastAPI"


def test_fallback_text_extract_skips_generic_more_about_links():
    class FakePage:
        async def evaluate(self, *_args):
            return [
                {
                    "title": "更多关于 reddit.com 的信息",
                    "url": "https://www.reddit.com/r/FastAPI/comments/1fm2hhk/cors_policy_no_accesscontrolalloworigin_header_is/",
                    "snippet": "",
                },
                {
                    "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
                    "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                    "snippet": "allow_origins - A list of origins that should be permitted.",
                },
            ]

    manager = BrowserManager(engine="brave", max_results=3)
    results = asyncio.run(manager._fallback_text_extract(FakePage(), "FastAPI CORS"))

    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["url"] == "https://fastapi.tiangolo.com/tutorial/cors/"


def test_search_result_postprocessing_resolves_sogou_wrappers_and_skips_search_pages(monkeypatch):
    wrapper_url = "https://www.sogou.com/link?url=opaque-article"
    search_wrapper_url = "https://www.sogou.com/link?url=opaque-more-content"
    article_url = "https://www.jb51.net/python/31727923x.htm"
    internal_search_url = (
        "https://www.sogou.com/web?ie=utf8&query=FastAPI%20CORSMiddleware%20allow_origins"
    )

    async def fake_resolve(url, log_func=None):
        if url == wrapper_url:
            return article_url
        if url == search_wrapper_url:
            return internal_search_url
        return url

    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve, raising=False)

    manager = BrowserManager(engine="sogou", max_results=3)
    results = asyncio.run(
        manager._postprocess_search_results(
            [
                {
                    "id": 1,
                    "title": "Python web框架fastapi中间件的使用及CORS跨域问题",
                    "url": wrapper_url,
                    "snippet": "allow_origins example",
                },
                {
                    "id": 2,
                    "title": "FastAPI CORSMiddleware allow_origins的更多内容_CSDN技术社区",
                    "url": search_wrapper_url,
                    "snippet": "more results",
                },
            ]
        )
    )

    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["url"] == article_url


def test_search_web_records_wait_selector_timeout_as_engine_failure(monkeypatch):
    class FakeMouse:
        async def move(self, *_args):
            return None

    class FakePage:
        mouse = FakeMouse()

        async def goto(self, *_args, **_kwargs):
            return None

        async def evaluate(self, *_args):
            return None

        async def content(self):
            return ""

        async def query_selector(self, *_args):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            raise TimeoutError("no results")

    @asynccontextmanager
    async def fake_rate_limit(_log_func=None):
        yield

    async def fake_release_page(_page):
        return None

    async def fake_get_new_page():
        return FakePage()

    engine_health._results.clear()
    monkeypatch.setattr(browser_manager, "get_context_pool_status", lambda: {"active_contexts": 1})
    monkeypatch.setattr(browser_manager, "get_new_page", fake_get_new_page)
    monkeypatch.setattr(browser_manager, "release_page", fake_release_page)
    monkeypatch.setattr(browser_manager, "search_rate_limit", fake_rate_limit)

    manager = BrowserManager(engine="searxng", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth

    assert asyncio.run(manager.search_web("FastAPI CORS")) == []
    assert engine_health.get_stats()["searxng"]["failures"] == 1


def test_search_web_uses_fallback_when_wait_selector_times_out(monkeypatch):
    class FakeMouse:
        async def move(self, *_args):
            return None

    class FakePage:
        mouse = FakeMouse()

        async def goto(self, *_args, **_kwargs):
            return None

        async def evaluate(self, *_args):
            return None

        async def content(self):
            return ""

        async def query_selector(self, *_args):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            raise TimeoutError("no results container")

    @asynccontextmanager
    async def fake_rate_limit(_log_func=None):
        yield

    async def fake_release_page(_page):
        return None

    async def fake_get_new_page():
        return FakePage()

    async def fake_resolve(url, log_func=None):
        return url

    async def fake_fallback(_page, _query, log_func=None):
        return [
            {
                "id": 1,
                "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
                "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                "snippet": "allow_origins - A list of origins that should be permitted.",
            }
        ]

    browser_manager._search_cache.clear()
    engine_health._results.clear()
    monkeypatch.setattr(browser_manager, "get_context_pool_status", lambda: {"active_contexts": 1})
    monkeypatch.setattr(browser_manager, "get_new_page", fake_get_new_page)
    monkeypatch.setattr(browser_manager, "release_page", fake_release_page)
    monkeypatch.setattr(browser_manager, "search_rate_limit", fake_rate_limit)
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)

    manager = BrowserManager(engine="brave", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth
    manager._fallback_text_extract = fake_fallback

    results = asyncio.run(manager.search_web("FastAPI CORS"))

    assert results == [
        {
            "id": 1,
            "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
            "url": "https://fastapi.tiangolo.com/tutorial/cors/",
            "snippet": "allow_origins - A list of origins that should be permitted.",
        }
    ]
    assert engine_health.get_stats()["brave"]["failures"] == 0


def test_engine_health_marks_engine_unhealthy_after_two_failures_by_default():
    monitor = EngineHealthMonitor()

    monitor.record("searxng", success=False)
    assert monitor.is_healthy("searxng") is True

    monitor.record("searxng", success=False)
    assert monitor.is_healthy("searxng") is False


def test_engine_health_prefers_stable_fallback_order_over_config_order():
    monitor = EngineHealthMonitor()
    monitor.record("searxng", success=False)
    monitor.record("searxng", success=False)

    fallback = monitor.get_fallback(
        "searxng",
        ["google", "bing", "duckduckgo", "sogou", "brave", "searxng"],
    )

    assert fallback == "duckduckgo"
