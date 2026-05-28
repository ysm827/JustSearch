import asyncio
import copy
import logging
import random
import time
import urllib.parse

from playwright.async_api import Page
from playwright_stealth import Stealth
from typing import List, Dict

from .browser_context import (
    init_global_browser, get_new_page, release_page,
    get_context_pool_status,
    search_rate_limit,
)
from .search_engine import load_selectors
from .engine_health import engine_health
from .interaction import (
    register_interaction_session, remove_interaction_session
)
from .llm_client import _truncate_for_log
from .crawler.redirects import resolve_redirect_url
from .page_crawler import crawl_page
from .search_result_cleanup import (
    clean_fallback_title,
    is_generic_search_aux_title,
    is_search_engine_internal_page,
)

# Simple search result cache: query -> (results, timestamp)
_search_cache: dict = {}
_SEARCH_CACHE_TTL = 300  # 5 minutes
_RESULTS_WAIT_TIMEOUT_MS = 15000
_MANUAL_VERIFICATION_TIMEOUT_SECONDS = 600.0
_MAX_MANUAL_VERIFICATION_STEPS = 3

logger = logging.getLogger(__name__)


def _clone_search_results(results: List[Dict]) -> List[Dict]:
    """Return an isolated copy so cached search results cannot be mutated by callers."""
    return copy.deepcopy(results)


def _search_failure_reason(error: Exception | str) -> str:
    """Classify search failures for engine health scoring."""
    text = str(error).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "other"


def _blocked_search_reason(content: str, current_url: str = "") -> str:
    """Return a reason when a search page is an anti-bot/error interstitial."""
    text = f"{current_url}\n{content}".lower()
    blocked_markers = (
        "static-pages/418.html",
        "unexpected error. please try again",
        "pow-captcha",
        "verifying your request",
        "正在验证您不是机器人",
        "在您继续搜索之前进行快速检查",
        "quick check before you continue searching",
        "sorry this pages exist in order to keep the service usable",
    )
    if any(marker in text for marker in blocked_markers):
        return "blocked"
    return ""


class BrowserManager:
    def __init__(self, engine: str = "searxng", max_results: int = 50):
        self.stealth = Stealth()
        self.engine = engine
        self.max_results = max_results
        # Load full engine config for fallback support, and single-engine config for direct use
        self.engine_config = load_selectors(None)  # full config dict (all engines)
        self.current_engine_config = self.engine_config.get(engine, load_selectors(engine))

    async def start(self):
        pool = get_context_pool_status()
        if pool["active_contexts"] == 0:
            await init_global_browser()

    async def stop(self):
        """No-op: browser contexts are managed by backend.app.browser_context."""
        return None

    async def search_web(
        self,
        query: str,
        log_func=None,
        session_id: str = None,
        allow_fallback: bool = True,
        use_cache: bool = True,
        health_batch_id: str | None = None,
    ) -> List[Dict]:
        """
        Concurrent Web Search - scrapes search results for the query.
        Automatically falls back to a healthy engine if the preferred one is unhealthy.
        """
        available_engines = list(self.engine_config.keys())
        actual_engine = (
            engine_health.get_fallback(self.engine, available_engines)
            if allow_fallback
            else self.engine
        )
        if actual_engine != self.engine:
            if log_func:
                log_func(f"浏览器: {self.engine.capitalize()} 不稳定，自动切换到 {actual_engine.capitalize()}")

        cache_key = f"{actual_engine}:{query}"
        cached = _search_cache.get(cache_key) if use_cache else None
        if cached and time.time() - cached[1] < _SEARCH_CACHE_TTL:
            if log_func:
                log_func(f"浏览器: 使用缓存搜索结果: '{_truncate_for_log(query)}'")
            engine_health.record(
                actual_engine,
                success=True,
                batch_id=health_batch_id,
            )
            return _clone_search_results(cached[0])

        pool = get_context_pool_status()
        if pool["active_contexts"] == 0:
            await self.start()

        config = self.engine_config.get(actual_engine, self.current_engine_config)
        engine_name = actual_engine.capitalize()

        page = await get_new_page()
        try:
            await self.stealth.apply_stealth_async(page)

            safe_query = _truncate_for_log(query)
            if log_func:
                log_func(f"浏览器: 正在前往 {engine_name} 搜索 '{safe_query}'...")

            delay = random.uniform(1.0, 2.0)
            await asyncio.sleep(delay)

            encoded_query = urllib.parse.quote(query)
            url = config["base_url"].format(query=encoded_query, num=self.max_results + 2)

            async with search_rate_limit(log_func):
                try:
                    if log_func:
                        log_func(f"浏览器: 正在加载搜索页面...")
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    if log_func:
                        log_func(f"浏览器: 搜索页面已加载，模拟用户行为...")
                    try:
                        await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                    except Exception:
                        pass
                except Exception as e:
                    if log_func:
                        log_func(f"浏览器: 搜索页面加载失败: {e}")
                    engine_health.record(
                        actual_engine,
                        success=False,
                        reason=_search_failure_reason(e),
                        batch_id=health_batch_id,
                    )
                    return []

            # Check for CAPTCHA
            content, current_url = await self._read_page_state(page)

            verification_ok = await self._handle_verification_pages(
                page=page,
                content=content,
                current_url=current_url,
                config=config,
                engine_name=engine_name,
                actual_engine=actual_engine,
                session_id=session_id,
                log_func=log_func,
                health_batch_id=health_batch_id,
            )
            if not verification_ok:
                return []

            # Wait for results
            try:
                await page.wait_for_selector(config["wait_selector"], timeout=_RESULTS_WAIT_TIMEOUT_MS)
                await asyncio.sleep(1.0)
            except Exception as e:
                msg = f"等待结果容器 ({config['wait_selector']}) 超时。"
                logger.warning(msg)
                if log_func:
                    log_func(f"浏览器错误: {msg}")
                    log_func("浏览器: 尝试使用降级文本提取搜索结果...")
                results = await self._fallback_text_extract(page, query, log_func)
                results = await self._postprocess_search_results(results, log_func=log_func)
                if results:
                    if log_func:
                        log_func(f"浏览器: 成功解析 {len(results)} 个结果。")
                    engine_health.record(
                        actual_engine,
                        success=True,
                        batch_id=health_batch_id,
                    )
                    if use_cache:
                        _search_cache[f"{actual_engine}:{query}"] = (_clone_search_results(results), time.time())
                    return results
                engine_health.record(
                    actual_engine,
                    success=False,
                    reason="selector",
                    batch_id=health_batch_id,
                )
                return []

            if log_func:
                log_func(f"浏览器: 正在解析结果...")

            # Extract results with retry logic
            results = []
            for attempt in range(3):
                try:
                    results = await page.evaluate(r"""([selectors, max_results]) => {
                        const results = [];
                        let count = 0;

                        let elements = [];
                        for (const sel of selectors.result_container) {
                            const found = document.querySelectorAll(sel);
                            if (found && found.length > 0) {
                                elements = Array.from(found);
                                break;
                            }
                        }

                        if (elements.length === 0) return results;

                        for (const el of elements) {
                            if (count >= max_results) break;

                            const titleEl = el.querySelector(selectors.title);
                            const linkEl = el.querySelector(selectors.link);
                            const snippetEl = el.querySelector(selectors.snippet);
                            const dateEl = selectors.date ? el.querySelector(selectors.date) : null;

                            if (!titleEl || !linkEl) continue;

                            const title = titleEl.innerText;
                            const url = linkEl.href;
                            let snippet = "";
                            let date = "";

                            if (dateEl) {
                                date = dateEl.innerText;
                            }

                            if (snippetEl) {
                                snippet = snippetEl.innerText;
                            } else {
                                let text = el.innerText;
                                if (text.includes(title)) text = text.replace(title, "");
                                snippet = text.trim().substring(0, 200);
                            }

                            if (!date && snippet) {
                                const dateMatch = snippet.match(/^([a-zA-Z]{3} \d{1,2}, \d{4}|\d{1,2} [a-zA-Z]{3} \d{4}|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2} hours? ago|\d{1,2} days? ago|\d+分钟前|\d+小时前|\d+天前|昨天|今天|\d{4}-\d{1,2}-\d{1,2})/);
                                if (dateMatch) {
                                    date = dateMatch[0];
                                }
                            }

                            if (url && url.startsWith('http')) {
                                count++;
                                results.push({
                                    id: count,
                                    title: title,
                                    url: url,
                                    snippet: snippet,
                                    date: date
                                });
                            }
                        }
                        return results;
                    }""", [config["selectors"], self.max_results])
                    break
                except Exception as e:
                    if "Execution context was destroyed" in str(e) or "Cannot find context" in str(e):
                        if attempt < 2:
                            if log_func:
                                log_func(f"浏览器: 页面上下文丢失，正在重试解析 ({attempt+1}/3)...")
                            await asyncio.sleep(1.0)
                            continue
                    raise e

            # 降级兜底：CSS 选择器解析无结果时，尝试从页面纯文本提取链接
            if not results:
                logger.info("CSS 选择器解析无结果，尝试降级文本提取...")
                results = await self._fallback_text_extract(page, query, log_func)

            results = await self._postprocess_search_results(results, log_func=log_func)

            if log_func:
                log_func(f"浏览器: 成功解析 {len(results)} 个结果。")
            if results:
                engine_health.record(
                    actual_engine,
                    success=True,
                    batch_id=health_batch_id,
                )
            else:
                engine_health.record(
                    actual_engine,
                    success=False,
                    reason="selector",
                    batch_id=health_batch_id,
                )
            # Cache results (use actual engine in key for consistency)
            if results and use_cache:
                _search_cache[f"{actual_engine}:{query}"] = (_clone_search_results(results), time.time())
            return results
        except Exception as e:
            msg = f"搜索错误: {e}"
            logger.error(msg)
            engine_health.record(
                actual_engine,
                success=False,
                reason=_search_failure_reason(e),
                batch_id=health_batch_id,
            )
            if log_func:
                log_func(f"浏览器错误: {msg}")
            return []
        finally:
            await release_page(page)

    async def _read_page_state(self, page: Page) -> tuple[str, str]:
        try:
            content = await page.content()
        except Exception:
            content = ""
        return content, getattr(page, "url", "")

    async def _detect_captcha(self, page: Page, content: str, config: Dict) -> bool:
        for check in config["captcha_check"]:
            if check.startswith("#"):
                try:
                    if await page.query_selector(check):
                        return True
                except Exception:
                    pass
            elif check in content:
                return True
        return False

    async def _wait_for_manual_verification(
        self,
        page: Page,
        session_id: str | None,
        log_func,
        action: str,
        message: str,
    ) -> bool:
        if not session_id:
            return False

        event = asyncio.Event()
        register_interaction_session(session_id, page, event)

        if log_func:
            log_func(f"ACTION_REQUIRED: {action}")
            log_func(message)

        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=_MANUAL_VERIFICATION_TIMEOUT_SECONDS,
            )
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            if log_func:
                log_func("浏览器: 收到验证完成信号，继续执行...")
            return True
        except asyncio.TimeoutError:
            if log_func:
                log_func("浏览器: 等待手动验证超时 (10分钟)。")
            return False
        finally:
            remove_interaction_session(session_id)

    async def _handle_verification_pages(
        self,
        page: Page,
        content: str,
        current_url: str,
        config: Dict,
        engine_name: str,
        actual_engine: str,
        session_id: str | None,
        log_func,
        health_batch_id: str | None,
    ) -> bool:
        for _ in range(_MAX_MANUAL_VERIFICATION_STEPS):
            if await self._detect_captcha(page, content, config):
                logger.warning("CAPTCHA detected on %s!", engine_name)
                engine_health.record(
                    actual_engine,
                    success=False,
                    reason="captcha",
                    batch_id=health_batch_id,
                )
                if log_func:
                    log_func("浏览器: 检测到验证码！等待手动解决...")

                verified = await self._wait_for_manual_verification(
                    page,
                    session_id,
                    log_func,
                    "CAPTCHA_DETECTED",
                    "浏览器: 请在弹出的手动验证窗口中解决验证码。",
                )
                if not verified:
                    return False
                content, current_url = await self._read_page_state(page)
                continue

            blocked_reason = _blocked_search_reason(content, current_url)
            if blocked_reason:
                logger.warning("Search blocked on %s: %s", engine_name, blocked_reason)
                engine_health.record(
                    actual_engine,
                    success=False,
                    reason=blocked_reason,
                    batch_id=health_batch_id,
                )
                if log_func:
                    log_func(f"浏览器: {engine_name} 返回验证/反爬页面，等待手动通过...")

                verified = await self._wait_for_manual_verification(
                    page,
                    session_id,
                    log_func,
                    "SEARCH_VERIFICATION_REQUIRED",
                    "浏览器: 请在弹出的手动验证窗口中通过搜索引擎验证。",
                )
                if not verified:
                    if log_func:
                        log_func(f"浏览器: {engine_name} 返回验证/错误页面，跳过该引擎。")
                    return False
                content, current_url = await self._read_page_state(page)
                continue

            return True

        if log_func:
            log_func(f"浏览器: {engine_name} 验证后仍未进入结果页，跳过该引擎。")
        return False

    async def _fallback_text_extract(self, page: Page, query: str, log_func=None) -> List[Dict]:
        """
        降级方案：当 CSS 选择器解析失败时，从页面纯文本 + 链接提取搜索结果。
        """
        try:
            items = await page.evaluate(r"""(maxResults) => {
                const results = [];
                const anchors = document.querySelectorAll('a[href^="http"]');
                function hostMatches(hostname, domain) {
                    return hostname === domain || hostname.endsWith('.' + domain);
                }

                function isSearchEngineUtilityUrl(href) {
                    try {
                        const parsed = new URL(href);
                        const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
                        if (hostMatches(host, 'google.com')) {
                            if (parsed.pathname === '/url') {
                                return !parsed.searchParams.has('url') && !parsed.searchParams.has('q');
                            }
                            return parsed.pathname === '/search';
                        }
                        if (hostMatches(host, 'bing.com')) {
                            return parsed.pathname === '/search';
                        }
                        if (hostMatches(host, 'duckduckgo.com')) {
                            if (parsed.pathname.startsWith('/l/')) {
                                return !parsed.searchParams.has('uddg');
                            }
                            return parsed.pathname === '/' && parsed.searchParams.has('q');
                        }
                        if (hostMatches(host, 'sogou.com')) {
                            return parsed.pathname === '/web';
                        }
                    } catch (e) {
                        return true;
                    }
                    return false;
                }

                let count = 0;
                for (const a of anchors) {
                    if (count >= maxResults) break;
                    const href = a.href;
                    // 跳过搜索引擎自身的导航链接，但保留 developers.google.com 等合法结果
                    if (isSearchEngineUtilityUrl(href)) continue;
                    const title = (a.innerText || a.textContent || '').trim();
                    if (title.length < 5 || title.length > 200) continue;
                    // 获取附近的文本作为摘要
                    const parent = a.closest('div, article, li') || a.parentElement;
                    const snippet = parent ? parent.innerText.replace(title, '').trim().substring(0, 200) : '';
                    results.push({ title, url: href, snippet });
                    count++;
                }
                return results;
            }""", self.max_results)

            # 给结果编号
            filtered_items = []
            for item in items:
                item['title'] = clean_fallback_title(item.get('title', ''), item.get('url', ''))
                if is_generic_search_aux_title(item['title']):
                    continue
                filtered_items.append(item)

            for i, item in enumerate(filtered_items):
                item['id'] = i + 1

            if filtered_items and log_func:
                log_func(f"浏览器: 降级文本提取到 {len(filtered_items)} 个结果。")
            return filtered_items
        except Exception as e:
            logger.warning("降级文本提取失败: %s", e)
            return []

    async def _postprocess_search_results(self, results: List[Dict], log_func=None) -> List[Dict]:
        """Resolve result-wrapper URLs and remove search-engine utility pages."""
        processed = []
        for item in results:
            url = item.get('url', '')
            if not url:
                continue

            resolved_url = await resolve_redirect_url(url, log_func=log_func)
            if is_search_engine_internal_page(resolved_url):
                continue

            new_item = item.copy()
            new_item['url'] = resolved_url
            processed.append(new_item)

        for i, item in enumerate(processed):
            item['id'] = i + 1

        return processed

    async def crawl_page(self, url: str, log_func=None, interactive_mode: bool = False,
                         query: str = None, llm_client=None, session_id: str = None) -> str:
        """Delegate to the page_crawler module."""
        return await crawl_page(
            url=url,
            stealth=self.stealth,
            log_func=log_func,
            interactive_mode=interactive_mode,
            query=query,
            llm_client=llm_client,
            session_id=session_id
        )
