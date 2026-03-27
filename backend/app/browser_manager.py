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
    _GLOBAL_CONTEXT, _SEARCH_LOCK, _LAST_REQUEST_TIME, _MIN_SEARCH_INTERVAL,
    init_global_browser, shutdown_global_browser, get_new_page
)
from .search_engine import load_selectors
from .interaction import (
    _INTERACTION_SESSIONS, get_interaction_session,
    mark_interaction_completed, register_interaction_session, remove_interaction_session
)
from .page_crawler import crawl_page

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self, engine: str = "duckduckgo", max_results: int = 8):
        self.stealth = Stealth()
        self.engine = engine
        self.max_results = max_results
        self.engine_config = load_selectors(engine)

    async def start(self):
        if not _GLOBAL_CONTEXT:
            await init_global_browser()

    async def stop(self):
        pass

    async def search_web(self, query: str, log_func=None, session_id: str = None) -> List[Dict]:
        """
        Concurrent Web Search - scrapes search results for the query.
        """
        if not _GLOBAL_CONTEXT:
            await self.start()

        config = self.engine_config.get(self.engine, self.engine_config["duckduckgo"])
        engine_name = self.engine.capitalize()

        page = await get_new_page()
        await self.stealth.apply_stealth_async(page)

        try:
            if log_func:
                log_func(f"浏览器: 正在前往 {engine_name} 搜索 '{query}'...")

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
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
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
                    results = await page.evaluate("""([selectors, max_results]) => {
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
                                const dateMatch = snippet.match(/^([a-zA-Z]{3} \d{1,2}, \d{4}|\d{1,2} [a-zA-Z]{3} \d{4}|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2} hours? ago|\d{1,2} days? ago)/);
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

            if log_func:
                log_func(f"浏览器: 成功解析 {len(results)} 个结果。")
            return results
        except Exception as e:
            msg = f"搜索错误: {e}"
            logger.error(msg)
            if log_func:
                log_func(f"浏览器错误: {msg}")
            return []
        finally:
            await page.close()

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
