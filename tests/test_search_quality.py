import base64
import asyncio

from backend.app import browser_manager
from backend.app import page_crawler
from backend.app import search_engine
from backend.app import workflow as workflow_module
from backend.app.crawler import content as crawler_content
from backend.app.crawler import redirects
from backend.app.crawler import security
from backend.app.browser_manager import BrowserManager
from backend.app.search_result_cleanup import is_search_engine_internal_page
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


def test_resolve_redirect_url_ignores_sogou_lookalike_domain(monkeypatch):
    wrapper_url = "https://evil-sogou.com/link?url=opaque-token"
    called = []

    async def fake_fetch(url):
        called.append(url)
        return '<script>window.location.replace("https://example.com/article")</script>'

    monkeypatch.setattr(redirects, "_fetch_sogou_redirect_html", fake_fetch, raising=False)

    assert asyncio.run(redirects.resolve_redirect_url(wrapper_url)) == wrapper_url
    assert called == []


def test_resolve_redirect_url_ignores_redirect_markers_on_non_engine_domains():
    target = "https://example.com/article"
    encoded_target = base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
    bing_lookalike = f"https://example.test/bing.com/ck/a?u=a1{encoded_target}"
    google_lookalike = (
        "https://example.test/google.com/url?q=https%3A%2F%2Fexample.com%2Farticle"
    )
    duck_lookalike = (
        "https://example.test/duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle"
    )

    assert asyncio.run(redirects.resolve_redirect_url(bing_lookalike)) == bing_lookalike
    assert asyncio.run(redirects.resolve_redirect_url(google_lookalike)) == google_lookalike
    assert asyncio.run(redirects.resolve_redirect_url(duck_lookalike)) == duck_lookalike


def test_resolve_redirect_url_extracts_google_target_param():
    google_url = "https://www.google.com/url?sa=t&q=https%3A%2F%2Fexample.com%2Farticle"

    assert asyncio.run(redirects.resolve_redirect_url(google_url)) == "https://example.com/article"


def test_resource_blocker_removed_in_bridge_refactor():
    # 桥接重构后 install_resource_blocker 已移除(真实浏览器不拦截资源)。
    # 保留 SSRF 守卫:is_private_url 仍用于在 navigate 前后校验目标 URL。
    assert not hasattr(crawler_content, "install_resource_blocker")
    assert hasattr(security, "is_private_url")


def test_crawl_page_blocks_private_url_after_browser_redirect(monkeypatch):
    # 桥接重构后 crawl_page 走 bridge.navigate + bridge.get_tab_url。
    # 导航到私有地址后,SSRF 守卫应拦截并返回错误,不进入内容提取。
    captured = {}

    class FakeTabPool:
        def __init__(self, client):
            self.client = client

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            captured["released"] = tab

        async def close_all_pending(self, session_id=None):
            captured["finalized"] = True

    class FakeBridge:
        async def navigate(self, tab_id, url, timeout_ms=20000):
            captured["navigated"] = url

        async def get_tab_url(self, tab_id):
            # 模拟浏览器跳转到了内网地址。
            return "http://127.0.0.1/admin"

    async def fake_resolve(url, log_func=None):
        return url

    monkeypatch.setattr(page_crawler, "TabPool", FakeTabPool)
    monkeypatch.setattr(page_crawler, "get_bridge_client", lambda: FakeBridge())
    monkeypatch.setattr(page_crawler, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(page_crawler, "is_private_url", lambda url: "127.0.0.1" in str(url))

    async def fake_extract(*_a, **_k):
        raise AssertionError("private redirect targets must not be extracted")

    monkeypatch.setattr(page_crawler, "extract_page_content", fake_extract)

    result = asyncio.run(
        page_crawler.crawl_page("https://public.example/start")
    )

    assert result == "错误: 不允许访问内网地址"
    assert captured.get("finalized") is True


def test_crawl_page_prefers_page_url_when_response_url_is_original(monkeypatch):
    # 跳转后 URL 与原 URL 一致(public),应进入正常提取流程。
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        async def navigate(self, tab_id, url, timeout_ms=20000):
            pass

        async def get_tab_url(self, tab_id):
            return "https://public.example/start"

        async def evaluate(self, tab_id, js, timeout_ms=None):
            return False

    async def fake_resolve(url, log_func=None):
        return url

    async def fake_extract(bridge, tab_id, url, log_func=None):
        return "public content"

    async def fake_og(bridge, tab_id):
        return {}

    monkeypatch.setattr(page_crawler, "TabPool", FakeTabPool)
    monkeypatch.setattr(page_crawler, "get_bridge_client", lambda: FakeBridge())
    monkeypatch.setattr(page_crawler, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(page_crawler, "is_private_url", lambda url: "127.0.0.1" in str(url))
    monkeypatch.setattr(page_crawler, "extract_page_content", fake_extract)
    monkeypatch.setattr(page_crawler, "extract_og_metadata", fake_og)

    result = asyncio.run(
        page_crawler.crawl_page("https://public.example/start")
    )

    assert result == "public content"


def test_crawl_page_skips_pdf_after_browser_redirect(monkeypatch):
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        async def navigate(self, tab_id, url, timeout_ms=20000):
            pass

        async def get_tab_url(self, tab_id):
            return "https://cdn.example/report.pdf?download=1"

    async def fake_resolve(url, log_func=None):
        return url

    async def fake_extract(*_a, **_k):
        raise AssertionError("redirected PDFs should not enter generic extraction")

    monkeypatch.setattr(page_crawler, "TabPool", FakeTabPool)
    monkeypatch.setattr(page_crawler, "get_bridge_client", lambda: FakeBridge())
    monkeypatch.setattr(page_crawler, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(page_crawler, "is_private_url", lambda url: False)
    monkeypatch.setattr(page_crawler, "extract_page_content", fake_extract)

    result = asyncio.run(
        page_crawler.crawl_page("https://public.example/report")
    )

    assert result == (
        "[PDF 文档] https://cdn.example/report.pdf?download=1\n"
        "注意: PDF 文件无法直接提取内容，请访问链接查看原文。"
    )


def test_private_url_blocks_direct_198_18_address_but_allows_proxy_resolved_domain(monkeypatch):
    def fake_getaddrinfo(hostname, *_args, **_kwargs):
        assert hostname == "example.test"
        return [(None, None, None, None, ("198.18.0.12", 0))]

    monkeypatch.setattr(security.socket, "getaddrinfo", fake_getaddrinfo)

    assert security.is_private_url("http://198.18.0.12/status") is True
    assert security.is_private_url("https://example.test/page") is False


def test_fallback_text_extract_cleans_multiline_search_titles(monkeypatch):
    # 桥接重构后 _fallback_text_extract(bridge, tab_id, ...) 内部走 bridge.evaluate。
    # 用一个 fake bridge 桩掉 evaluate,返回固定的锚点列表。
    class FakeBridge:
        async def evaluate(self, _tab_id, _js, timeout_ms=None):
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
    results = asyncio.run(manager._fallback_text_extract(FakeBridge(), tab_id=1, query="FastAPI CORS"))

    assert results[0]["title"] == "CORS (Cross-Origin Resource Sharing) - FastAPI"


def test_fallback_text_extract_skips_generic_more_about_links(monkeypatch):
    class FakeBridge:
        async def evaluate(self, _tab_id, _js, timeout_ms=None):
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
    results = asyncio.run(manager._fallback_text_extract(FakeBridge(), tab_id=1, query="FastAPI CORS"))

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


def test_search_result_postprocessing_skips_common_search_engine_internal_pages(monkeypatch):
    async def fake_resolve(url, log_func=None):
        return url

    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve, raising=False)

    manager = BrowserManager(engine="google", max_results=10)
    results = asyncio.run(
        manager._postprocess_search_results(
            [
                {
                    "id": 1,
                    "title": "Google Search",
                    "url": "https://www.google.com/search?q=FastAPI",
                    "snippet": "",
                },
                {
                    "id": 2,
                    "title": "Bing Search",
                    "url": "https://www.bing.com/search?q=FastAPI",
                    "snippet": "",
                },
                {
                    "id": 3,
                    "title": "DuckDuckGo Search",
                    "url": "https://duckduckgo.com/?q=FastAPI",
                    "snippet": "",
                },
                {
                    "id": 4,
                    "title": "Brave Search",
                    "url": "https://search.brave.com/search?q=FastAPI",
                    "snippet": "",
                },
                {
                    "id": 5,
                    "title": "FastAPI CORS",
                    "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                    "snippet": "allow_origins controls allowed origins.",
                },
            ]
        )
    )

    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["url"] == "https://fastapi.tiangolo.com/tutorial/cors/"


def test_search_engine_internal_page_detection_preserves_real_subdomain_results():
    assert is_search_engine_internal_page("https://www.google.com/search?q=FastAPI") is True
    assert is_search_engine_internal_page("https://developers.google.com/search?q=FastAPI") is False
    assert is_search_engine_internal_page("https://learn.microsoft.com/search/?terms=FastAPI") is False


def test_search_web_records_wait_selector_timeout_as_selector_failure(monkeypatch):
    # 桥接重构后 search_web 走 bridge + TabPool。用 fake bridge 桩掉:
    # - navigate 成功
    # - evaluate 永远返回 0(结果容器不存在)→ 走降级 → 也为空 → selector 失败
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        async def init(self, wait_timeout=0.0):
            return False

        async def navigate(self, tab_id, url, timeout_ms=20000):
            return {"tabId": tab_id, "url": url}

        async def scroll_by(self, *a, **k):
            pass

        async def evaluate(self, tab_id, js, timeout_ms=None):
            # 结果容器长度=0,降级提取也返回空列表。
            if "function(selectors" in js:
                return []
            if ".length" in js:
                return 0
            return []

        async def get_tab_url(self, tab_id):
            return "https://searxng.example/search"

    monkeypatch.setattr(browser_manager, "TabPool", FakeTabPool)
    monkeypatch.setattr(browser_manager, "get_bridge_client", lambda: FakeBridge())

    manager = BrowserManager(engine="searxng", max_results=3)

    assert asyncio.run(manager.search_web("FastAPI CORS")) == []


def test_search_web_records_verification_page_as_blocked(monkeypatch):
    # 检测到反爬页面(blocked):_read_page_state 返回包含标记的 content,
    # 验证码/反爬轮询会一直等(这里把超时压到 0 让它快速返回)。
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        async def init(self, wait_timeout=0.0):
            return False

        async def navigate(self, tab_id, url, timeout_ms=20000):
            return {"tabId": tab_id, "url": url}

        async def scroll_by(self, *a, **k):
            pass

        async def evaluate(self, tab_id, js, timeout_ms=None):
            return None

        async def get_tab_url(self, tab_id):
            return "https://brave.example/search"

    async def fake_read_page_state(self, bridge, tab_id):
        return "正在验证您不是机器人 在您继续搜索之前进行快速检查。 pow-captcha", ""

    monkeypatch.setattr(browser_manager, "TabPool", FakeTabPool)
    monkeypatch.setattr(browser_manager, "get_bridge_client", lambda: FakeBridge())
    monkeypatch.setattr(
        browser_manager.BrowserManager, "_read_page_state", fake_read_page_state
    )
    # 把验证等待超时压到 0,立即失败返回 []。
    monkeypatch.setattr(browser_manager, "_MANUAL_VERIFICATION_TIMEOUT_SECONDS", 0.0)

    manager = BrowserManager(engine="brave", max_results=3)

    assert asyncio.run(manager.search_web("FastAPI CORS", allow_fallback=False)) == []


def test_blocked_search_page_with_session_opens_manual_verification_and_continues(monkeypatch):
    # 桥接重构后验证码改为轮询检测:第一次 _read_page_state 返回反爬标记,
    # 轮询时第二次返回干净(用户已通过验证),搜索继续。
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        async def init(self, wait_timeout=0.0):
            return False

        async def navigate(self, tab_id, url, timeout_ms=20000):
            return {"tabId": tab_id, "url": url}

        async def scroll_by(self, *a, **k):
            pass

        async def evaluate(self, tab_id, js, timeout_ms=None):
            # 提取 IIFE 以 (function(selectors 开头;wait-selector 探针只是 querySelectorAll(...).length。
            # 必须先判 IIFE,因为 IIFE 内部也含 .length。
            if "function(selectors" in js:
                return [
                    {
                        "id": 1,
                        "title": "CORS (Cross-Origin Resource Sharing) - FastAPI",
                        "url": "https://fastapi.tiangolo.com/tutorial/cors/",
                        "snippet": "allow_origins controls allowed origins.",
                    }
                ]
            if ".length" in js:
                return 1
            return None

        async def get_tab_url(self, tab_id):
            return "https://brave.example/search"

    async def fake_resolve(url, log_func=None):
        return url

    logs = []
    call_count = {"n": 0}

    async def fake_read_page_state(self, bridge, tab_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "正在验证您不是机器人 在您继续搜索之前进行快速检查。 pow-captcha", ""
        # 轮询期间:干净页面 → 验证通过。
        return "", ""

    monkeypatch.setattr(browser_manager, "TabPool", FakeTabPool)
    monkeypatch.setattr(browser_manager, "get_bridge_client", lambda: FakeBridge())
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(
        browser_manager.BrowserManager, "_read_page_state", fake_read_page_state
    )

    manager = BrowserManager(engine="brave", max_results=3)

    results = asyncio.run(
        manager.search_web(
            "FastAPI CORS",
            allow_fallback=False,
            session_id="session-1",
            log_func=logs.append,
        )
    )

    assert results[0]["title"] == "CORS (Cross-Origin Resource Sharing) - FastAPI"
    assert any("ACTION_REQUIRED: SEARCH_VERIFICATION_REQUIRED" in msg for msg in logs)
    assert any("收到验证完成信号" in msg for msg in logs)


def test_google_captcha_without_session_returns_without_waiting(monkeypatch):
    # 桥接重构后验证码改为轮询检测。无 session_id 时仍会提示用户手动解决,
    # 但不会注册 interaction session(已移除)。这里验证:
    # - _read_page_state 返回 CAPTCHA 标记
    # - 轮询超时后返回 []
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        async def init(self, wait_timeout=0.0):
            return False

        async def navigate(self, tab_id, url, timeout_ms=20000):
            return {"tabId": tab_id, "url": url}

        async def scroll_by(self, *a, **k):
            pass

        async def evaluate(self, tab_id, js, timeout_ms=None):
            return None

        async def get_tab_url(self, tab_id):
            return "https://www.google.com/search?q=FastAPI%20CORS"

    async def fake_read_page_state(self, bridge, tab_id):
        return "unusual traffic from your computer network", ""

    monkeypatch.setattr(browser_manager, "TabPool", FakeTabPool)
    monkeypatch.setattr(browser_manager, "get_bridge_client", lambda: FakeBridge())
    monkeypatch.setattr(
        browser_manager.BrowserManager, "_read_page_state", fake_read_page_state
    )
    # 把验证等待超时压到 0,立即失败返回 []。
    monkeypatch.setattr(browser_manager, "_MANUAL_VERIFICATION_TIMEOUT_SECONDS", 0.0)

    manager = BrowserManager(engine="google", max_results=3)

    assert asyncio.run(manager.search_web("FastAPI CORS", allow_fallback=False)) == []


def test_search_web_cache_is_isolated_from_returned_results(monkeypatch):
    # 桥接重构后 search_web 走 bridge + TabPool。验证缓存隔离。
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        def __init__(self):
            self.loaded_urls = []

        async def init(self, wait_timeout=0.0):
            return False

        async def navigate(self, tab_id, url, timeout_ms=20000):
            self.loaded_urls.append(url)
            return {"tabId": tab_id, "url": url}

        async def scroll_by(self, *a, **k):
            pass

        async def evaluate(self, tab_id, js, timeout_ms=None):
            # 提取 IIFE 以 (function(selectors 开头;wait-selector 探针只是 querySelectorAll(...).length。
            # 必须先判 IIFE,因为 IIFE 内部也含 .length。
            if "function(selectors" in js:
                return [
                    {
                        "id": 1,
                        "title": "Original cached title",
                        "url": "https://example.com/original",
                        "snippet": "cache isolation",
                    }
                ]
            if ".length" in js:
                return 1
            return None

        async def get_tab_url(self, tab_id):
            return "https://searx.be/search?q=cache%20isolation&format=html"

    async def fake_resolve(url, log_func=None):
        return url

    browser_manager._search_cache.clear()

    fake_bridge = FakeBridge()
    monkeypatch.setattr(browser_manager, "TabPool", FakeTabPool)
    monkeypatch.setattr(browser_manager, "get_bridge_client", lambda: fake_bridge)
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(browser_manager.random, "uniform", lambda *_args: 0)

    manager = BrowserManager(engine="searxng", max_results=3)

    first = asyncio.run(manager.search_web("cache isolation"))
    first[0]["title"] = "Mutated by caller"

    second = asyncio.run(manager.search_web("cache isolation"))

    assert second[0]["title"] == "Original cached title"
    assert fake_bridge.loaded_urls == ["https://searx.be/search?q=cache%20isolation&format=html"]


def test_search_web_can_check_preferred_engine_without_fallback_or_cache(monkeypatch):
    # 桥接重构后 search_web 走 bridge + TabPool。验证 allow_fallback=False 时不回退。
    class FakeTabPool:
        def __init__(self, client):
            pass

        async def acquire(self, session_id=None):
            return {"tab_id": 1}

        async def release(self, tab):
            pass

        async def close_all_pending(self, session_id=None):
            pass

    class FakeBridge:
        def __init__(self):
            self.loaded_urls = []

        async def init(self, wait_timeout=0.0):
            return False

        async def navigate(self, tab_id, url, timeout_ms=20000):
            self.loaded_urls.append(url)
            return {"tabId": tab_id, "url": url}

        async def scroll_by(self, *a, **k):
            pass

        async def evaluate(self, tab_id, js, timeout_ms=None):
            # 提取 IIFE 以 (function(selectors 开头;wait-selector 探针只是 querySelectorAll(...).length。
            # 必须先判 IIFE,因为 IIFE 内部也含 .length。
            if "function(selectors" in js:
                return [
                    {
                        "id": 1,
                        "title": "JustSearch",
                        "url": "https://example.com/justsearch",
                        "snippet": "test result",
                    }
                ]
            if ".length" in js:
                return 1
            return None

        async def get_tab_url(self, tab_id):
            return "https://search.brave.com/search?q=JustSearch%20test"

    async def fake_resolve(url, log_func=None):
        return url

    browser_manager._search_cache.clear()

    fake_bridge = FakeBridge()
    monkeypatch.setattr(browser_manager, "TabPool", FakeTabPool)
    monkeypatch.setattr(browser_manager, "get_bridge_client", lambda: fake_bridge)
    monkeypatch.setattr(browser_manager, "resolve_redirect_url", fake_resolve)
    monkeypatch.setattr(browser_manager.random, "uniform", lambda *_args: 0)

    manager = BrowserManager(engine="brave", max_results=3)

    results = asyncio.run(
        manager.search_web(
            "JustSearch test",
            allow_fallback=False,
            use_cache=False,
        )
    )

    assert results[0]["title"] == "JustSearch"
    assert fake_bridge.loaded_urls == ["https://search.brave.com/search?q=JustSearch%20test"]
    assert "brave:JustSearch test" not in browser_manager._search_cache


def test_searxng_search_url_can_be_overridden_for_self_hosting(monkeypatch):
    monkeypatch.setenv(
        "SEARXNG_SEARCH_URL",
        "http://searxng:8080/search?q={query}&format=html",
    )
    monkeypatch.setattr(search_engine, "_config_cache", {})
    monkeypatch.setattr(search_engine, "_config_mtime", 0.0)

    config = search_engine.load_selectors("searxng")

    assert config["base_url"] == "http://searxng:8080/search?q={query}&format=html"


def test_searxng_search_url_override_applies_to_fallback_config(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SEARXNG_SEARCH_URL",
        "http://searxng:8080/search?q={query}&format=html",
    )
    monkeypatch.setattr(search_engine, "__file__", str(tmp_path / "search_engine.py"))
    monkeypatch.setattr(search_engine, "_config_cache", {})
    monkeypatch.setattr(search_engine, "_config_mtime", 0.0)

    config = search_engine.load_selectors("searxng")

    assert config["base_url"] == "http://searxng:8080/search?q={query}&format=html"
    assert search_engine.get_all_engines() == ["searxng"]


def test_search_selector_hot_reload_keeps_last_good_config_on_bad_json(monkeypatch, tmp_path):
    config_path = tmp_path / "search_selectors.json"
    config_path.write_text(
        """
        {
            "searxng": {
                "base_url": "https://searx.example/search?q={query}",
                "selectors": {
                    "result_container": [".result"],
                    "title": "h3",
                    "link": "a",
                    "snippet": ".snippet",
                    "date": ""
                },
                "captcha_check": [],
                "wait_selector": ".result"
            },
            "custom": {
                "base_url": "https://custom.example/search?q={query}",
                "selectors": {
                    "result_container": [".item"],
                    "title": "h2",
                    "link": "a",
                    "snippet": "p",
                    "date": ""
                },
                "captcha_check": [],
                "wait_selector": ".result"
            }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(search_engine, "__file__", str(tmp_path / "search_engine.py"))
    monkeypatch.setattr(search_engine, "_config_cache", {})
    monkeypatch.setattr(search_engine, "_config_mtime", 0.0)

    loaded = search_engine.load_selectors(None)
    assert loaded["custom"]["base_url"] == "https://custom.example/search?q={query}"

    config_path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(search_engine, "_config_mtime", -1)

    reloaded = search_engine.load_selectors(None)

    assert reloaded["custom"]["base_url"] == "https://custom.example/search?q={query}"
    assert search_engine.get_all_engines() == ["searxng", "custom"]


def test_search_selector_hot_reload_keeps_last_good_config_on_bad_shape(monkeypatch, tmp_path):
    config_path = tmp_path / "search_selectors.json"
    config_path.write_text(
        """
        {
            "searxng": {
                "base_url": "https://searx.example/search?q={query}",
                "selectors": {
                    "result_container": ".result",
                    "title": "h3",
                    "link": "a",
                    "snippet": ".snippet"
                },
                "captcha_check": [],
                "wait_selector": ".result"
            },
            "custom": {
                "base_url": "https://custom.example/search?q={query}",
                "selectors": {
                    "result_container": [".item"],
                    "title": "h2",
                    "link": "a",
                    "snippet": "p"
                },
                "captcha_check": [],
                "wait_selector": ".item"
            }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(search_engine, "__file__", str(tmp_path / "search_engine.py"))
    monkeypatch.setattr(search_engine, "_config_cache", {})
    monkeypatch.setattr(search_engine, "_config_mtime", 0.0)

    loaded = search_engine.load_selectors(None)
    assert loaded["custom"]["base_url"] == "https://custom.example/search?q={query}"

    config_path.write_text(
        """
        {
            "broken": {
                "base_url": "https://broken.example/search?q={query}",
                "selectors": {
                    "result_container": []
                },
                "captcha_check": [],
                "wait_selector": ""
            }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(search_engine, "_config_mtime", -1)

    reloaded = search_engine.load_selectors(None)

    assert reloaded["custom"]["base_url"] == "https://custom.example/search?q={query}"
    assert search_engine.get_all_engines() == ["searxng", "custom"]


def test_search_selector_loader_skips_invalid_engines(monkeypatch, tmp_path):
    config_path = tmp_path / "search_selectors.json"
    config_path.write_text(
        """
        {
            "broken": {
                "base_url": "",
                "selectors": {},
                "captcha_check": [],
                "wait_selector": ".result"
            },
            "custom": {
                "base_url": "https://custom.example/search?q={query}",
                "selectors": {
                    "result_container": ".item",
                    "title": "h2",
                    "link": "a",
                    "snippet": "p"
                },
                "captcha_check": "captcha",
                "wait_selector": ".item"
            }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(search_engine, "__file__", str(tmp_path / "search_engine.py"))
    monkeypatch.setattr(search_engine, "_config_cache", {})
    monkeypatch.setattr(search_engine, "_config_mtime", 0.0)

    loaded = search_engine.load_selectors(None)

    assert list(loaded.keys()) == ["custom"]
    assert loaded["custom"]["selectors"]["result_container"] == [".item"]
    assert loaded["custom"]["captcha_check"] == ["captcha"]


def test_workflow_records_batch_timeout_returns_empty(monkeypatch):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(10)

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


def test_workflow_skips_crawl_when_no_results_are_relevant():
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


def test_workflow_crawl_batch_keeps_successful_pages_when_one_fails():
    class FakeBrowser:
        async def crawl_page(self, url, **_kwargs):
            if url == "https://example.com/bad":
                raise RuntimeError("boom")
            return f"content for {url}"

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="searxng",
        max_results=3,
    )
    workflow.browser = FakeBrowser()
    progress = []

    sources, counter = asyncio.run(
        workflow._crawl_and_collect(
            [
                {"id": 1, "title": "Good", "url": "https://example.com/good"},
                {"id": 2, "title": "Bad", "url": "https://example.com/bad"},
            ],
            set(),
            progress.append,
            "query",
            0,
        )
    )

    assert counter == 1
    assert sources == [
        {
            "id": 1,
            "title": "Good",
            "url": "https://example.com/good",
            "date": "",
            "content": "content for https://example.com/good",
        }
    ]
    assert any("跳过爬取异常页面" in item for item in progress)


def test_workflow_source_ids_do_not_skip_failed_pages():
    class FakeBrowser:
        async def crawl_page(self, url, **_kwargs):
            if url.endswith("/empty"):
                return ""
            return f"content for {url}"

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="searxng",
        max_results=3,
    )
    workflow.browser = FakeBrowser()

    sources, counter = asyncio.run(
        workflow._crawl_and_collect(
            [
                {"id": 1, "title": "Empty", "url": "https://example.com/empty"},
                {"id": 2, "title": "Good", "url": "https://example.com/good"},
            ],
            set(),
            lambda _msg: None,
            "query",
            0,
        )
    )

    assert counter == 1
    assert [source["id"] for source in sources] == [1]


def test_workflow_skips_crawler_error_strings():
    class FakeBrowser:
        async def crawl_page(self, *_args, **_kwargs):
            return "爬取页面时出错: boom"

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="searxng",
        max_results=3,
    )
    workflow.browser = FakeBrowser()
    progress = []

    sources, counter = asyncio.run(
        workflow._crawl_and_collect(
            [
                {"id": 1, "title": "Broken", "url": "https://example.com/broken"},
            ],
            set(),
            progress.append,
            "query",
            0,
        )
    )

    assert sources == []
    assert counter == 0
    assert workflow._content_cache == {}
    assert any("跳过无效页面" in item for item in progress)


def test_workflow_direct_url_skips_blocked_private_error():
    class FakeBrowser:
        async def crawl_page(self, *_args, **_kwargs):
            return "错误: 不允许访问内网地址"

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="searxng",
        max_results=3,
    )
    workflow.browser = FakeBrowser()
    progress = []

    sources, counter = asyncio.run(
        workflow._handle_direct_url(
            "http://127.0.0.1/admin",
            set(),
            progress.append,
            "query",
            0,
        )
    )

    assert sources == []
    assert counter == 0
    assert any("跳过无效页面" in item for item in progress)


def test_workflow_decodes_bing_redirect_with_following_query_params():
    target_url = "https://Example.com/Docs?A=1"
    encoded_target = base64.urlsafe_b64encode(target_url.encode("utf-8")).decode("ascii").rstrip("=")
    bing_url = f"https://www.bing.com/ck/a?u=a1{encoded_target}&ntb=1"
    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        search_engine="searxng",
        max_results=3,
    )

    assert workflow._resolve_url(bing_url) == target_url
    assert workflow._normalize_url(bing_url) == "https://example.com/docs?a=1"


def test_workflow_deduplicates_visited_urls_after_normalization():
    class FakeLLM:
        async def assess_relevance(self, _query, _snippets):
            return [1, 2]

    class FakeBrowser:
        engine = "searxng"
        engine_config = {"searxng": {}}

        async def search_web(self, *_args, **_kwargs):
            return [
                {
                    "id": 1,
                    "title": "Tracked",
                    "url": "https://example.com/page?utm_source=newsletter",
                    "snippet": "tracked",
                },
                {
                    "id": 2,
                    "title": "Canonical",
                    "url": "https://example.com/page",
                    "snippet": "canonical",
                },
            ]

        async def crawl_page(self, *_args, **_kwargs):
            raise AssertionError("already visited canonical URL should not be crawled")

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
            ["example page"],
            [],
            {"https://example.com/page"},
            1,
            lambda _msg: None,
            "example page",
            0,
        )
    )

    assert sources == []
    assert counter == 0
    assert result_count == 1


def test_workflow_routes_llm_calls_to_configured_step_clients():
    calls = []

    class AnalysisLLM:
        async def analyze_task(self, _query, _history):
            calls.append("analysis")
            return {"type": "search", "queries": ["FastAPI middleware"]}

    class AnswerLLM:
        total_prompt_tokens = 11
        total_completion_tokens = 7

        async def generate_answer(self, _query, _sources, _history, _stream_callback, canvas_mode=False, live_artifacts_mode=False):
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


def test_workflow_does_not_append_markdown_references_to_live_artifacts():
    class AnalysisLLM:
        async def analyze_task(self, _query, _history):
            return {"type": "search", "queries": ["Live Artifacts"]}

    class AnswerLLM:
        total_prompt_tokens = 2
        total_completion_tokens = 3

        async def generate_answer(
            self,
            _query,
            _sources,
            _history,
            _stream_callback,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            assert live_artifacts_mode is True
            return {
                "status": "sufficient",
                "answer": '<section style="display:block;width:100%;box-sizing:border-box;max-width:100%;overflow-wrap:anywhere;"><h2>Live</h2><p>引用 [1]</p></section>',
            }

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="fallback-model",
        search_engine="searxng",
        max_results=3,
        live_artifacts_mode=True,
    )
    workflow.step_llms["analysis"] = AnalysisLLM()
    workflow.step_llms["answer"] = AnswerLLM()

    async def fake_handle_search(*_args, **_kwargs):
        return (
            [
                {
                    "id": 1,
                    "title": "Live Artifacts source",
                    "url": "https://example.com/live-artifacts",
                    "content": "Live Artifacts source content.",
                }
            ],
            1,
            1,
        )

    workflow._handle_search = fake_handle_search

    result = asyncio.run(
        workflow.run(
            "Live Artifacts",
            lambda _msg: None,
            None,
            [],
            None,
            lambda _data: None,
        )
    )

    assert result.startswith("<section")
    assert "### 参考资料" not in result
    assert "\n\n---" not in result


def test_workflow_keeps_partial_live_artifact_answers_in_artifact_format():
    class AnalysisLLM:
        async def analyze_task(self, _query, _history):
            return {"type": "search", "queries": ["Live Artifacts partial"]}

    class AnswerLLM:
        total_prompt_tokens = 2
        total_completion_tokens = 3

        async def generate_answer(
            self,
            _query,
            _sources,
            _history,
            _stream_callback,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            assert live_artifacts_mode is True
            return {
                "status": "insufficient",
                "missing_info": "缺少第二个独立来源",
                "answer": "## 临时结论\n- 已找到一个来源 [1]",
            }

    workflow = SearchWorkflow(
        api_key="test",
        base_url="https://example.test/v1",
        model="fallback-model",
        search_engine="searxng",
        max_results=3,
        max_iterations=1,
        live_artifacts_mode=True,
    )
    workflow.step_llms["analysis"] = AnalysisLLM()
    workflow.step_llms["answer"] = AnswerLLM()

    async def fake_handle_search(*_args, **_kwargs):
        return (
            [
                {
                    "id": 1,
                    "title": "Live Artifacts source",
                    "url": "https://example.com/live-artifacts",
                    "content": "Live Artifacts source content.",
                }
            ],
            1,
            1,
        )

    workflow._handle_search = fake_handle_search

    result = asyncio.run(
        workflow.run(
            "Live Artifacts partial",
            lambda _msg: None,
            None,
            [],
            None,
            lambda _data: None,
        )
    )

    assert result.startswith("<section")
    assert "临时结论" in result
    assert "### 参考资料" not in result
    assert "\n\n---" not in result


def test_workflow_returns_partial_answer_when_time_limit_hits_after_insufficient_result(monkeypatch):
    class AnalysisLLM:
        async def analyze_task(self, _query, _history):
            return {"type": "search", "queries": ["SearXNG 是什么"]}

    class AnswerLLM:
        total_prompt_tokens = 3
        total_completion_tokens = 5

        async def generate_answer(self, _query, _sources, _history, _stream_callback, canvas_mode=False, live_artifacts_mode=False):
            return {
                "status": "insufficient",
                "missing_info": "缺少官方隐私说明",
                "answer": "SearXNG 是一个免费的开源元搜索引擎。",
            }

    monkeypatch.setattr(workflow_module, "_MAX_TOTAL_SEARCH_SECONDS", 0.01)

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
        await asyncio.sleep(0.02)
        return (
            [
                {
                    "id": 1,
                    "title": "SearXNG docs",
                    "url": "https://docs.searxng.org/",
                    "content": "SearXNG is a free internet metasearch engine.",
                }
            ],
            1,
            1,
        )

    workflow._handle_search = fake_handle_search
    progress = []
    stats = {}

    result = asyncio.run(
        workflow.run(
            "SearXNG 是什么",
            progress.append,
            None,
            [],
            None,
            lambda data: stats.update(data),
        )
    )

    assert "已达到本次搜索的时间限制" in result
    assert "SearXNG 是一个免费的开源元搜索引擎。" in result
    assert "多次尝试后未能生成有效答案" not in result
    assert "[SearXNG docs](https://docs.searxng.org/)" in result
    assert any("已超过总搜索时限" in item for item in progress)
    assert stats["sites_searched"] == 1
    assert stats["prompt_tokens"] == 3
    assert stats["completion_tokens"] == 5
