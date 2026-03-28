import asyncio
import logging
import os
import random
import time
import urllib.parse

from playwright.async_api import Page
from playwright_stealth import Stealth
from typing import List, Dict

from .browser_context import (
    _SEARCH_LOCK, _LAST_REQUEST_TIME, _MIN_SEARCH_INTERVAL,
    init_global_browser, shutdown_global_browser, get_new_page, release_page,
    get_context_pool_status,
)
from .search_engine import load_selectors
from .engine_health import engine_health
from .interaction import (
    _INTERACTION_SESSIONS, get_interaction_session,
    mark_interaction_completed, register_interaction_session, remove_interaction_session
)
from .llm_client import _truncate_for_log
from .page_crawler import crawl_page

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self, engine: str = "duckduckgo", max_results: int = 8):
        self.stealth = Stealth()
        self.engine = engine
        self.max_results = max_results
        self.engine_config = load_selectors(engine)

    async def start(self):
        pool = get_context_pool_status()
        if pool["active_contexts"] == 0:
            await init_global_browser()

    async def stop(self):
        pass

    async def search_web(self, query: str, log_func=None, session_id: str = None) -> List[Dict]:
        """
        Concurrent Web Search - scrapes search results for the query.
        """
        pool = get_context_pool_status()
        if pool["active_contexts"] == 0:
            await self.start()

        config = self.engine_config.get(self.engine, self.engine_config["duckduckgo"])
        engine_name = self.engine.capitalize()

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

            # Rate limiting
            global _LAST_REQUEST_TIME
            async with _SEARCH_LOCK:
                now = time.time()
                elapsed = now - _LAST_REQUEST_TIME
                if elapsed < _MIN_SEARCH_INTERVAL:
                    wait_time = _MIN_SEARCH_INTERVAL - elapsed + random.uniform(0.5, 1.5)
                    if log_func:
                        log_func(f"浏览器: 正在排队等待搜索 (冷却 {wait_time:.1f}s)...")
                    await asyncio.sleep(wait_time)

                _LAST_REQUEST_TIME = time.time()

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
                    return []

            # Check for CAPTCHA
            try:
                content = await page.content()
            except Exception:
                content = ""

            detected_captcha = False
            for check in config["captcha_check"]:
                if check.startswith("#"):
                    try:
                        if await page.query_selector(check):
                            detected_captcha = True
                            break
                    except Exception:
                        pass
                elif check in content:
                    detected_captcha = True
                    break

            if detected_captcha:
                logger.warning("CAPTCHA detected on %s!", engine_name)
                if log_func:
                    log_func("浏览器: 检测到验证码！等待手动解决...")

                if session_id:
                    if log_func:
                        log_func("ACTION_REQUIRED: CAPTCHA_DETECTED")

                    event = asyncio.Event()
                    register_interaction_session(session_id, page, event)

                    if log_func:
                        log_func(f"浏览器: 请点击界面上的'手动验证'按钮来解决验证码")

                    try:
                        await asyncio.wait_for(event.wait(), timeout=600.0)
                        if log_func:
                            log_func("浏览器: 收到验证完成信号，继续执行...")
                    except asyncio.TimeoutError:
                        if log_func:
                            log_func("浏览器: 等待手动验证超时 (10分钟)。")
                    finally:
                        remove_interaction_session(session_id)
                else:
                    try:
                        await page.wait_for_selector(config["wait_selector"], timeout=60000)
                        if log_func:
                            log_func("浏览器: 验证码已解决，继续...")
                    except asyncio.TimeoutError:
                        if log_func:
                            log_func("浏览器: 验证码未及时解决。")

            # Wait for results
            try:
                await page.wait_for_selector(config["wait_selector"], timeout=30000)
                await asyncio.sleep(1.0)
            except Exception as e:
                msg = f"等待结果容器 ({config['wait_selector']}) 超时。"
                logger.warning(msg)
                if log_func:
                    log_func(f"浏览器错误: {msg}")
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

            if log_func:
                log_func(f"浏览器: 成功解析 {len(results)} 个结果。")
            engine_health.record(engine, success=True)
            return results
        except Exception as e:
            msg = f"搜索错误: {e}"
            logger.error(msg)
            engine_health.record(engine, success=False)
            if log_func:
                log_func(f"浏览器错误: {msg}")
            return []
        finally:
            await release_page(page)

    async def _fallback_text_extract(self, page: Page, query: str, log_func=None) -> List[Dict]:
        """
        降级方案：当 CSS 选择器解析失败时，从页面纯文本 + 链接提取搜索结果。
        """
        try:
            items = await page.evaluate(r"""(maxResults) => {
                const results = [];
                const anchors = document.querySelectorAll('a[href^="http"]');
                // 搜索引擎自身域名，用于过滤
                const engineDomains = ['google.com', 'bing.com', 'duckduckgo.com', 'sogou.com'];
                let count = 0;
                for (const a of anchors) {
                    if (count >= maxResults) break;
                    const href = a.href;
                    // 跳过搜索引擎自身的导航链接和域名
                    if (href.includes('search?') || href.includes('/l/')) continue;
                    if (engineDomains.some(d => href.includes(d))) continue;
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
            for i, item in enumerate(items):
                item['id'] = i + 1

            if items and log_func:
                log_func(f"浏览器: 降级文本提取到 {len(items)} 个结果。")
            return items
        except Exception as e:
            logger.warning("降级文本提取失败: %s", e)
            return []

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
