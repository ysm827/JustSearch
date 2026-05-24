import asyncio
from contextlib import asynccontextmanager

from backend.app import browser_manager
from backend.app import workflow as workflow_module
from backend.app.crawler import redirects
from backend.app.browser_manager import BrowserManager
from backend.app.engine_health import EngineHealthMonitor, engine_health
from backend.app.workflow import SearchWorkflow


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


def test_search_web_records_wait_selector_timeout_as_selector_failure(monkeypatch):
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
    stats = engine_health.get_stats()["searxng"]
    assert stats["failures"] == 1
    assert stats["healthy"] is True
    assert stats["failure_reasons"] == {"selector": 1}
    assert stats["failure_score"] == 1.0
    assert stats["failure_streak"] == 1
    assert stats["critical_failure_streak"] == 0


def test_search_web_records_verification_page_as_blocked(monkeypatch):
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
            return "正在验证您不是机器人 在您继续搜索之前进行快速检查。 pow-captcha"

        async def query_selector(self, *_args):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            raise AssertionError("blocked pages should return before waiting for results")

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

    manager = BrowserManager(engine="brave", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth

    assert asyncio.run(manager.search_web("FastAPI CORS", allow_fallback=False)) == []
    stats = engine_health.get_stats()["brave"]
    assert stats["failure_reasons"] == {"blocked": 1}
    assert stats["critical_failure_streak"] == 1


def test_blocked_search_page_with_session_opens_manual_verification_and_continues(monkeypatch):
    class FakeMouse:
        async def move(self, *_args):
            return None

    class FakePage:
        mouse = FakeMouse()

        def __init__(self):
            self.content_calls = 0

        async def goto(self, *_args, **_kwargs):
            return None

        async def evaluate(self, script, *_args):
            if "querySelectorAll" not in script:
                return None
            return [
                {
                    "id": 1,
                    "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
                    "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                    "snippet": "allow_origins controls allowed origins.",
                }
            ]

        async def content(self):
            self.content_calls += 1
            if self.content_calls == 1:
                return "正在验证您不是机器人 在您继续搜索之前进行快速检查。 pow-captcha"
            return ""

        async def query_selector(self, *_args):
            return None

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def fake_rate_limit(_log_func=None):
        yield

    async def fake_release_page(_page):
        return None

    fake_page = FakePage()

    async def fake_get_new_page():
        return fake_page

    async def fake_resolve(url, log_func=None):
        return url

    registered_sessions = []
    removed_sessions = []

    def fake_register(session_id, page, event):
        registered_sessions.append((session_id, page))
        asyncio.get_running_loop().call_soon(event.set)

    def fake_remove(session_id):
        removed_sessions.append(session_id)

    logs = []

    engine_health._results.clear()
    monkeypatch.setattr(browser_manager, "get_context_pool_status", lambda: {"active_contexts": 1})
    monkeypatch.setattr(browser_manager, "get_new_page", fake_get_new_page)
    monkeypatch.setattr(browser_manager, "release_page", fake_release_page)
    monkeypatch.setattr(browser_manager, "search_rate_limit", fake_rate_limit)
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(browser_manager, "register_interaction_session", fake_register)
    monkeypatch.setattr(browser_manager, "remove_interaction_session", fake_remove)

    manager = BrowserManager(engine="brave", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth

    results = asyncio.run(
        manager.search_web(
            "FastAPI CORS",
            allow_fallback=False,
            session_id="session-1",
            log_func=logs.append,
        )
    )

    assert results[0]["title"] == "CORS (Cross-Origin Resource Sharing) - FastAPI"
    assert registered_sessions == [("session-1", fake_page)]
    assert removed_sessions == ["session-1"]
    assert any("ACTION_REQUIRED: SEARCH_VERIFICATION_REQUIRED" in msg for msg in logs)
    assert any("收到验证完成信号" in msg for msg in logs)


def test_google_captcha_without_session_returns_without_waiting(monkeypatch):
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
            return "unusual traffic from your computer network"

        async def query_selector(self, *_args):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            raise AssertionError("non-interactive CAPTCHA should fail fast")

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

    manager = BrowserManager(engine="google", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth

    assert asyncio.run(manager.search_web("FastAPI CORS", allow_fallback=False)) == []
    stats = engine_health.get_stats()["google"]
    assert stats["failure_reasons"] == {"captcha": 1}
    assert stats["critical_failure_streak"] == 1


def test_search_web_uses_fallback_after_engine_is_marked_unhealthy(monkeypatch):
    class FakeMouse:
        async def move(self, *_args):
            return None

    class FakePage:
        mouse = FakeMouse()

        def __init__(self):
            self.loaded_urls = []

        async def goto(self, url, **_kwargs):
            self.loaded_urls.append(url)
            return None

        async def evaluate(self, script, *_args):
            if "querySelectorAll" not in script:
                return None
            return [
                {
                    "id": 1,
                    "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
                    "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                    "snippet": "allow_origins - A list of origins that should be permitted.",
                }
            ]

        async def content(self):
            return ""

        async def query_selector(self, *_args):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def fake_rate_limit(_log_func=None):
        yield

    async def fake_release_page(_page):
        return None

    fake_page = FakePage()

    async def fake_get_new_page():
        return fake_page

    async def fake_resolve(url, log_func=None):
        return url

    browser_manager._search_cache.clear()
    engine_health._results.clear()
    engine_health.record("brave", success=False, reason="timeout")
    engine_health.record("brave", success=False, reason="timeout")
    engine_health.record("brave", success=False, reason="timeout")
    monkeypatch.setattr(browser_manager, "get_context_pool_status", lambda: {"active_contexts": 1})
    monkeypatch.setattr(browser_manager, "get_new_page", fake_get_new_page)
    monkeypatch.setattr(browser_manager, "release_page", fake_release_page)
    monkeypatch.setattr(browser_manager, "search_rate_limit", fake_rate_limit)
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)

    manager = BrowserManager(engine="brave", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth

    results = asyncio.run(manager.search_web("FastAPI CORS"))

    assert results == [
        {
            "id": 1,
            "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
            "url": "https://fastapi.tiangolo.com/tutorial/cors/",
            "snippet": "allow_origins - A list of origins that should be permitted.",
        }
    ]
    assert fake_page.loaded_urls == ["https://www.bing.com/search?q=FastAPI%20CORS"]
    assert engine_health.get_stats()["brave"]["healthy"] is False


def test_search_web_can_check_preferred_engine_without_fallback_or_cache(monkeypatch):
    class FakeMouse:
        async def move(self, *_args):
            return None

    class FakePage:
        mouse = FakeMouse()

        def __init__(self):
            self.loaded_urls = []

        async def goto(self, url, **_kwargs):
            self.loaded_urls.append(url)
            return None

        async def evaluate(self, script, *_args):
            if "querySelectorAll" not in script:
                return None
            return [
                {
                    "id": 1,
                    "title": "JustSearch",
                    "url": "https://example.com/justsearch",
                    "snippet": "test result",
                }
            ]

        async def content(self):
            return ""

        async def query_selector(self, *_args):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def fake_rate_limit(_log_func=None):
        yield

    async def fake_release_page(_page):
        return None

    fake_page = FakePage()

    async def fake_get_new_page():
        return fake_page

    async def fake_resolve(url, log_func=None):
        return url

    browser_manager._search_cache.clear()
    engine_health._results.clear()
    engine_health.record("brave", success=False, reason="timeout")
    engine_health.record("brave", success=False, reason="timeout")
    engine_health.record("brave", success=False, reason="timeout")
    monkeypatch.setattr(browser_manager, "get_context_pool_status", lambda: {"active_contexts": 1})
    monkeypatch.setattr(browser_manager, "get_new_page", fake_get_new_page)
    monkeypatch.setattr(browser_manager, "release_page", fake_release_page)
    monkeypatch.setattr(browser_manager, "search_rate_limit", fake_rate_limit)
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)

    manager = BrowserManager(engine="brave", max_results=3)

    async def fake_apply_stealth(_page):
        return None

    manager.stealth.apply_stealth_async = fake_apply_stealth

    results = asyncio.run(
        manager.search_web(
            "JustSearch test",
            allow_fallback=False,
            use_cache=False,
        )
    )

    assert results[0]["title"] == "JustSearch"
    assert fake_page.loaded_urls == ["https://search.brave.com/search?q=JustSearch%20test"]
    assert "brave:JustSearch test" not in browser_manager._search_cache


def test_engine_health_treats_single_selector_failure_as_soft_signal():
    monitor = EngineHealthMonitor()

    monitor.record("searxng", success=False, reason="selector")
    assert monitor.is_healthy("searxng") is True

    stats = monitor.get_stats()["searxng"]
    assert stats["failure_score"] == 1.0
    assert stats["failure_streak"] == 1
    assert stats["critical_failure_streak"] == 0


def test_engine_health_treats_blocked_as_critical_failure():
    monitor = EngineHealthMonitor()

    monitor.record("brave", success=False, reason="blocked")

    stats = monitor.get_stats()["brave"]
    assert stats["failure_reasons"] == {"blocked": 1}
    assert stats["failure_score"] == 3.0
    assert stats["critical_failure_streak"] == 1


def test_engine_health_treats_low_quality_as_soft_signal():
    monitor = EngineHealthMonitor()

    monitor.record("searxng", success=False, reason="low_quality")
    monitor.record("searxng", success=False, reason="low_quality")

    stats = monitor.get_stats()["searxng"]
    assert stats["failure_reasons"] == {"low_quality": 2}
    assert stats["failure_score"] == 2.0
    assert stats["critical_failure_streak"] == 0
    assert monitor.is_healthy("searxng") is True


def test_engine_health_keeps_engine_healthy_after_two_timeout_failures():
    monitor = EngineHealthMonitor()

    monitor.record("searxng", success=False, reason="timeout")
    assert monitor.is_healthy("searxng") is True

    monitor.record("searxng", success=False, reason="timeout")
    assert monitor.is_healthy("searxng") is True


def test_engine_health_marks_engine_unhealthy_after_three_timeout_failures():
    monitor = EngineHealthMonitor()

    monitor.record("searxng", success=False, reason="timeout")
    monitor.record("searxng", success=False, reason="timeout")
    monitor.record("searxng", success=False, reason="timeout")

    assert monitor.is_healthy("searxng") is False


def test_engine_health_softens_batch_timeouts_when_batch_has_success():
    monitor = EngineHealthMonitor()
    batch_id = "node-docs"

    monitor.record("brave", success=True, batch_id=batch_id)
    monitor.record("brave", success=False, reason="timeout", batch_id=batch_id)
    monitor.record("brave", success=False, reason="timeout", batch_id=batch_id)

    stats = monitor.get_stats()["brave"]
    assert stats["failures"] == 2
    assert stats["failure_reasons"] == {"batch_soft_timeout": 2}
    assert stats["failure_score"] == 1.0
    assert stats["failure_streak"] == 0
    assert stats["critical_failure_streak"] == 0
    assert monitor.is_healthy("brave") is True


def test_engine_health_marks_engine_unhealthy_after_three_selector_failures():
    monitor = EngineHealthMonitor()

    monitor.record("searxng", success=False, reason="selector")
    monitor.record("searxng", success=False, reason="selector")
    assert monitor.is_healthy("searxng") is True

    monitor.record("searxng", success=False, reason="selector")
    assert monitor.is_healthy("searxng") is False


def test_engine_health_success_resets_failure_streaks():
    monitor = EngineHealthMonitor()

    monitor.record("brave", success=False, reason="timeout")
    monitor.record("brave", success=True)

    stats = monitor.get_stats()["brave"]
    assert stats["failures"] == 1
    assert stats["failure_streak"] == 0
    assert stats["critical_failure_streak"] == 0
    assert monitor.is_healthy("brave") is True


def test_engine_health_prefers_stable_fallback_order_over_config_order():
    monitor = EngineHealthMonitor()
    monitor.record("searxng", success=False, reason="timeout")
    monitor.record("searxng", success=False, reason="timeout")
    monitor.record("searxng", success=False, reason="timeout")

    fallback = monitor.get_fallback(
        "searxng",
        ["google", "bing", "duckduckgo", "sogou", "brave", "searxng"],
    )

    assert fallback == "bing"


def test_workflow_records_batch_timeouts_in_engine_health(monkeypatch):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(10)

    engine_health._results.clear()
    monkeypatch.setattr(workflow_module, "_SEARCH_BATCH_TIMEOUT_SECONDS", 0.01)

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="brave",
        max_results=3,
    )
    workflow.browser.search_web = never_finishes

    sources, counter, result_count = asyncio.run(
        workflow._handle_search(
            ["FastAPI CORS", "FastAPI CORSMiddleware"],
            [],
            set(),
            1,
            lambda _msg: None,
            "FastAPI CORS",
            0,
        )
    )

    assert sources == []
    assert counter == 0
    assert result_count == 0
    stats = engine_health.get_stats()["brave"]
    assert stats["failure_reasons"] == {"timeout": 2}


def test_workflow_records_low_quality_and_skips_crawl_when_no_results_are_relevant():
    class FakeLLM:
        async def assess_relevance(self, _query, _snippets):
            return []

    class FakeBrowser:
        engine = "searxng"
        engine_config = {"searxng": {}}

        async def search_web(self, *_args, **_kwargs):
            return [
                {
                    "id": 1,
                    "title": "Unrelated cooking tips",
                    "url": "https://example.com/cooking",
                    "snippet": "Pasta sauces and dinner ideas.",
                }
            ]

        async def crawl_page(self, *_args, **_kwargs):
            raise AssertionError("irrelevant search results should not be crawled")

    engine_health._results.clear()
    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="searxng",
        max_results=3,
    )
    workflow.llm = FakeLLM()
    workflow.browser = FakeBrowser()

    sources, counter, result_count = asyncio.run(
        workflow._handle_search(
            ["FastAPI CORS official docs"],
            [],
            set(),
            1,
            lambda _msg: None,
            "FastAPI CORS official docs",
            0,
        )
    )

    assert sources == []
    assert counter == 0
    assert result_count == 1
    stats = engine_health.get_stats()["searxng"]
    assert stats["failure_reasons"] == {"low_quality": 1}
    assert stats["healthy"] is True


def test_workflow_routes_llm_calls_to_configured_step_clients():
    calls = []

    class AnalysisLLM:
        async def analyze_task(self, _query, _history):
            calls.append("analysis")
            return {"type": "search", "queries": ["FastAPI middleware"]}

    class AnswerLLM:
        total_prompt_tokens = 11
        total_completion_tokens = 7

        async def generate_answer(self, _query, _sources, _history, _stream_callback):
            calls.append("answer")
            return {"status": "sufficient", "answer": "done"}

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="fallback-model",
        search_engine="searxng",
        max_results=3,
    )
    workflow.step_llms["analysis"] = AnalysisLLM()
    workflow.step_llms["answer"] = AnswerLLM()

    async def fake_handle_search(*_args, **_kwargs):
        return (
            [
                {
                    "id": 1,
                    "title": "FastAPI",
                    "url": "https://example.com/fastapi",
                    "content": "FastAPI middleware reference.",
                }
            ],
            1,
            1,
        )

    workflow._handle_search = fake_handle_search
    stats = {}

    result = asyncio.run(
        workflow.run(
            "FastAPI middleware",
            lambda _msg: None,
            None,
            [],
            None,
            lambda data: stats.update(data),
        )
    )

    assert "done" in result
    assert calls == ["analysis", "answer"]
    assert stats["prompt_tokens"] == 11
    assert stats["completion_tokens"] == 7
