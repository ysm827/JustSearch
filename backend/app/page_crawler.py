import asyncio
import base64
import json
import logging
import os
import random
import urllib.parse

from playwright.async_api import Page
from playwright_stealth import Stealth

from .browser_context import get_new_page, _GLOBAL_CONTEXT
from .interaction import register_interaction_session, remove_interaction_session

logger = logging.getLogger(__name__)


async def resolve_redirect_url(url: str, log_func=None) -> str:
    """Resolve search engine redirect URLs (DuckDuckGo/Bing/Google) to final URLs."""
    final_url = url
    if "bing.com/ck/a" not in url and "google.com/url" not in url and "duckduckgo.com/l/" not in url:
        return final_url

    if log_func:
        log_func("浏览器: 检测到重定向 URL，正在尝试提取目标...")

    # Handle DuckDuckGo
    if "duckduckgo.com/l/" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if 'uddg' in params:
                final_url = params['uddg'][0]
                if log_func:
                    log_func(f"浏览器: 提取 DuckDuckGo 重定向 URL 成功: {final_url}")
        except Exception as e:
            if log_func:
                log_func(f"浏览器: 提取 DuckDuckGo 重定向 URL 失败: {e}")

    # For Bing, the 'u' parameter is often base64 encoded with 'a1' prefix
    elif "bing.com/ck/a" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if 'u' in params:
            u_val = params['u'][0]
            if u_val.startswith('a1'):
                try:
                    b64_part = u_val[2:]
                    b64_part += "=" * ((4 - len(b64_part) % 4) % 4)
                    decoded = base64.b64decode(b64_part).decode('utf-8')
                    final_url = decoded
                    if log_func:
                        log_func(f"浏览器: 提取 Bing 重定向 URL 成功: {final_url}")
                except Exception as e:
                    if log_func:
                        log_func(f"浏览器: 提取 Bing 重定向 URL 失败: {e}")

    return final_url


async def crawl_github_api(page: Page, url: str, log_func=None) -> str | None:
    """Handle GitHub API URLs - parse JSON and return a summary."""
    if log_func:
        log_func("浏览器: 检测到 GitHub API 请求，正在优化数据...")
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        json_content = await page.evaluate("() => document.body.innerText")
        data = json.loads(json_content)
        if isinstance(data, list):
            summary = f"GitHub API Repository List Summary (First 30 items):\n"
            total_stars = 0
            for repo in data:
                name = repo.get("name", "Unknown")
                stars = repo.get("stargazers_count", 0)
                desc = repo.get("description", "")
                total_stars += stars
                summary += f"- {name}: {stars} stars ({desc})\n"

            summary += f"\nTotal stars in this page: {total_stars}\n"

            if len(data) == 30:
                summary += "WARNING: There are likely more repositories (pagination detected). This count is INCOMPLETE.\n"

            if log_func:
                log_func(f"浏览器: 成功解析 GitHub API 数据，当前页共 {total_stars} stars。")
            return summary
    except json.JSONDecodeError:
        pass
    except Exception as e:
        if log_func:
            log_func(f"浏览器: GitHub API 处理失败: {e}")
    return None


async def extract_github_repo_stats(page: Page, url: str, log_func=None) -> str | None:
    """Extract star counts from GitHub repository list pages."""
    try:
        await page.wait_for_selector("#user-repositories-list", timeout=5000)

        repo_stats = await page.evaluate("""() => {
            let totalStars = 0;
            let repos = [];

            const items = document.querySelectorAll('li[itemprop="owns"], .source, .public');

            items.forEach(item => {
                const starLink = item.querySelector('a[href*="/stargazers"]');
                if (starLink) {
                    const text = starLink.innerText.trim().replace(/,/g, '');
                    const stars = parseInt(text);
                    if (!isNaN(stars)) {
                        totalStars += stars;
                        const nameEl = item.querySelector('a[itemprop="name codeRepository"], h3 a');
                        const name = nameEl ? nameEl.innerText.trim() : "Unknown";
                        repos.push({name, stars});
                    }
                }
            });

            return {totalStars, repos, count: repos.length};
        }""")

        if repo_stats and repo_stats.get('count', 0) > 0:
            if log_func:
                log_func(f"浏览器: 页面内统计到 {repo_stats['count']} 个仓库，共 {repo_stats['totalStars']} stars。")

            prepend_text = f"--- AUTOMATED ANALYSIS ---\n"
            prepend_text += f"Total Stars visible on this page: {repo_stats['totalStars']}\n"
            prepend_text += f"Repository Count visible: {repo_stats['count']}\n"
            prepend_text += f"Top Repositories (First few):\n"
            for r in repo_stats['repos']:
                prepend_text += f"- {r['name']}: {r['stars']} stars\n"
            prepend_text += f"--------------------------\n\n"
            return prepend_text
        else:
            if log_func:
                log_func("浏览器: 未能在页面上提取到 Star 数据。")
    except Exception as e:
        if log_func:
            log_func(f"浏览器: GitHub 页面分析失败: {e}")
    return None


async def run_interactive_mode(page: Page, query: str, llm_client, log_func=None):
    """Run interactive mode: extract clickable elements and ask LLM what to click."""
    if log_func:
        log_func("浏览器: 交互模式已开启，正在提取可点击元素...")

    elements = await page.evaluate("""() => {
        const items = [];
        let idCounter = 0;

        function isVisible(elem) {
            if (!elem.getBoundingClientRect || !elem.checkVisibility) return false;
            const rect = elem.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && elem.checkVisibility();
        }

        const candidates = document.querySelectorAll('button, a[href], [role="button"]');

        for (const el of candidates) {
            if (!isVisible(el)) continue;

            const text = el.innerText.trim();
            if (text.length < 2 || text.length > 50) continue;

            if (/^(home|login|sign in|sign up|menu|privacy|terms)$/i.test(text)) continue;

            const tempId = "js-interact-" + idCounter++;
            el.setAttribute("data-js-interact-id", tempId);

            items.push({
                id: tempId,
                text: text,
                tag: el.tagName.toLowerCase()
            });

            if (items.length >= 50) break;
        }
        return items;
    }""")

    if elements:
        if log_func:
            log_func(f"浏览器: 提取到 {len(elements)} 个候选元素。请求 AI 决策...")

        clicked_ids = await llm_client.decide_click_elements(query, elements)

        if clicked_ids:
            if log_func:
                log_func(f"浏览器: AI 决定点击元素 ID: {clicked_ids}")

            for cid in clicked_ids:
                try:
                    await page.click(f'[data-js-interact-id="{cid}"]', timeout=2000)
                    if log_func:
                        log_func(f"浏览器: 已点击元素 {cid}")
                    await asyncio.sleep(1.0)
                except Exception as e:
                    if log_func:
                        log_func(f"浏览器: 点击元素 {cid} 失败: {e}")

            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                await asyncio.sleep(2.0)
        else:
            if log_func:
                log_func("浏览器: AI 决定不点击任何元素。")
    else:
        if log_func:
            log_func("浏览器: 未找到显著的可交互元素。")


async def extract_page_content(page: Page, url: str) -> str:
    """Extract text content from a page with retry logic for context issues."""
    for attempt in range(3):
        try:
            return await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception as e:
            if "Execution context was destroyed" in str(e) or "Cannot find context" in str(e):
                if attempt < 2:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        await asyncio.sleep(2)
                    continue
            logger.error("Extraction error on %s: %s", url, e)
            break
    return ""


async def crawl_page(url: str, stealth: Stealth, log_func=None,
                     interactive_mode: bool = False, query: str = None,
                     llm_client=None, session_id: str = None) -> str:
    """
    Headless Browser Deep Crawling - resolves redirects, loads page, extracts content.
    """
    if not _GLOBAL_CONTEXT:
        from .browser_context import init_global_browser
        await init_global_browser()

    final_url = await resolve_redirect_url(url, log_func)

    page = await get_new_page()
    await stealth.apply_stealth_async(page)

    try:
        if log_func:
            log_func(f"浏览器: 正在爬取 {final_url}...")

        # Special handling for GitHub API
        if "api.github.com" in final_url and "/repos" in final_url:
            result = await crawl_github_api(page, final_url, log_func)
            if result is not None:
                return result

        try:
            await page.goto(final_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            if log_func:
                log_func(f"浏览器: 加载页面超时或失败 {final_url}: {e}")
            return ""

        # Wait for content to stabilize
        prepend_text = ""
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
            if "github.com" in final_url and "tab=repositories" in final_url:
                prepend_text = await extract_github_repo_stats(page, final_url, log_func) or ""
        except Exception:
            pass

        # Interactive Mode
        if interactive_mode and query and llm_client:
            try:
                await run_interactive_mode(page, query, llm_client, log_func)
            except Exception as e:
                if log_func:
                    log_func(f"浏览器: 交互模式执行出错: {e}")

        content = await extract_page_content(page, url)

        if prepend_text:
            content = prepend_text + content

        if log_func:
            log_func(f"浏览器: 已爬取 {url} - 提取了 {len(content)} 个字符。")
        return content.strip()

    except Exception as e:
        msg = f"Crawl error for {url}: {e}"
        logger.error(msg)
        if log_func:
            log_func(f"浏览器错误: {msg}")
        return f"爬取页面时出错: {str(e)}"
    finally:
        await page.close()
