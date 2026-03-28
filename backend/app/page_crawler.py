import asyncio
import base64
import ipaddress
import json
import logging
import os
import random
import socket
import urllib.parse

from playwright.async_api import Page
from playwright_stealth import Stealth

from .browser_context import get_new_page, release_page, get_context_pool_status
from .interaction import register_interaction_session, remove_interaction_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DOM-density content extraction script (runs in browser context)
# Replaces raw document.body.innerText with a smarter extractor that:
#   1. Removes noise (nav, footer, sidebar, ads, comments, etc.)
#   2. Finds the highest text-density container (article/main/.content/...)
#   3. Falls back to cleaned body if no good candidate found
# ---------------------------------------------------------------------------
_JS_EXTRACT_CONTENT = """() => {
    const NOISE = [
        'nav','footer','aside','header',
        '[role="navigation"]','[role="banner"]','[role="contentinfo"]',
        '.sidebar','.nav-bar','.footer','.site-header',
        '.ad','.ads','.advertisement','.sponsor','.promoted',
        '.cookie-banner','.cookie-notice','.popup','.modal-overlay',
        '.social-share','.share-buttons','.related-posts','.related-articles',
        '.comments','#comments','.comment-section',
        '.breadcrumb','.pagination','.pager',
        'iframe','script','style','noscript','svg'
    ];

    const clone = document.body.cloneNode(true);
    NOISE.forEach(sel => {
        try { clone.querySelectorAll(sel).forEach(el => el.remove()); } catch(e) {}
    });

    function textLen(el) { return (el.innerText || '').replace(/\\s+/g,'').length; }
    function tagCount(el) { return el.getElementsByTagName('*').length; }
    function density(el) { return textLen(el) / (tagCount(el) + 1); }

    const candidates = clone.querySelectorAll(
        'article, main, [role="main"], .content, .post-content, .entry-content, ' +
        '.article-content, .post-body, #content, #main, .main-content, .page-content'
    );

    let best = clone, bestD = density(clone);
    candidates.forEach(el => {
        const d = density(el);
        if (d > bestD) { bestD = d; best = el; }
    });

    let text = (best.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();

    // Minimum content threshold — fall back to cleaned body
    if (text.replace(/\\s+/g,'').length < 200) {
        text = (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
    }

    // Collect downloadable links (dmg, exe, zip, pkg, etc.)
    // and links whose visible text suggests a download
    const downloadExts = ['.dmg', '.exe', '.zip', '.pkg', '.deb', '.rpm', '.apk', '.msix', '.tar.gz', '.tar.xz', '.7z', '.iso', '.appx'];
    const downloadKeywords = /download|下载|安装包|installer|离线/i;
    const collectedLinks = [];
    const seenUrls = new Set();

    best.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
        const linkText = (a.innerText || '').trim();

        const isDownloadUrl = downloadExts.some(ext => href.toLowerCase().includes(ext));
        const isDownloadText = downloadKeywords.test(linkText);

        if (isDownloadUrl || isDownloadText) {
            if (!seenUrls.has(href)) {
                seenUrls.add(href);
                const label = linkText || href;
                collectedLinks.push({ text: label.substring(0, 80), url: href });
            }
        }
    });

    // Append collected download links if found
    if (collectedLinks.length > 0) {
        text += '\\n\\n--- 页面中的下载链接 ---';
        for (const link of collectedLinks) {
            text += '\\n[' + link.text + '](' + link.url + ')';
        }
    }

    return text;
}"""

# Private network ranges to block
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_private_url(url: str) -> bool:
    """Check if a URL points to a private/internal network address."""
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True  # No hostname = invalid

        # Block localhost variants
        if hostname in ("localhost", "localhost.localdomain"):
            return True

        # Block IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1)
        if hostname.startswith("::ffff:"):
            mapped_ipv4 = hostname[7:]
            try:
                ip = ipaddress.ip_address(mapped_ipv4)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return True
            except ValueError:
                pass

        # Try to resolve hostname and check if IP is private
        addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addrinfo:
            ip = ipaddress.ip_address(sockaddr[0])
            # 跳过代理/VPN 虚拟 IP 段 (198.18.0.0/15)
            # 本地代理工具（Surge/Clash等）会劫持 DNS 将域名解析到此段
            if ip in ipaddress.ip_network("198.18.0.0/15"):
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError, OSError):
        # DNS 解析失败时拒绝，因为无法确定目标是否安全
        return True

    return False


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

    # Extract elements and store their positions (no DOM mutation)
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

            if (/^(home|login|sign in|sign up|menu|privacy|terms|登录|注册|分享|首页|关闭|关闭|评论)$/i.test(text)) continue;

            const rect = el.getBoundingClientRect();
            const tempId = "js-interact-" + idCounter++;

            items.push({
                id: tempId,
                text: text,
                tag: el.tagName.toLowerCase(),
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2
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

            # Build position lookup
            pos_map = {}
            for el in elements:
                pos_map[el['id']] = (el['x'], el['y'])

            for cid in clicked_ids:
                if cid not in pos_map:
                    continue
                x, y = pos_map[cid]
                clicked = False
                # Retry up to 3 times with increasing timeout
                for attempt in range(3):
                    try:
                        await page.mouse.click(x, y, timeout=5000)
                        if log_func:
                            log_func(f"浏览器: 已点击元素 {cid}")
                        clicked = True
                        await asyncio.sleep(1.0)
                        break
                    except Exception as e:
                        if attempt < 2:
                            if log_func:
                                log_func(f"浏览器: 点击 {cid} 失败 (重试 {attempt+1}/3): {e}")
                            await asyncio.sleep(0.5 * (attempt + 1))
                        else:
                            if log_func:
                                log_func(f"浏览器: 点击元素 {cid} 最终失败: {e}")

                if clicked:
                    # Wait for any dynamic content to load after click
                    try:
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        await asyncio.sleep(1.0)
        else:
            if log_func:
                log_func("浏览器: AI 决定不点击任何元素。")
    else:
        if log_func:
            log_func("浏览器: 未找到显著的可交互元素。")


async def extract_page_content(page: Page, url: str) -> str:
    """Extract main content from a page using DOM-density algorithm with retry logic."""
    for attempt in range(3):
        try:
            return await page.evaluate(_JS_EXTRACT_CONTENT)
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


# Resource types to block during content crawling (speeds up page load significantly)
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


async def _install_resource_blocker(page: Page):
    """Abort requests for non-essential resources (images, fonts, media, CSS)."""
    await page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES
        else route.continue_(),
    )


async def crawl_page(url: str, stealth: Stealth, log_func=None,
                     interactive_mode: bool = False, query: str = None,
                     llm_client=None, session_id: str = None) -> str:
    """
    Headless Browser Deep Crawling - resolves redirects, loads page, extracts content.
    """
    pool = get_context_pool_status()
    if pool["active_contexts"] == 0:
        from .browser_context import init_global_browser
        await init_global_browser()

    final_url = await resolve_redirect_url(url, log_func)

    # SSRF protection: block private/internal network addresses
    if is_private_url(final_url):
        if log_func:
            log_func(f"浏览器: 拒绝访问内网地址 {final_url}")
        return "错误: 不允许访问内网地址"

    page = await get_new_page()
    await stealth.apply_stealth_async(page)
    # Block non-essential resources to speed up crawling
    await _install_resource_blocker(page)

    try:
        if log_func:
            log_func(f"浏览器: 正在爬取 {final_url}...")

        # Special handling for GitHub API
        if "api.github.com" in final_url and "/repos" in final_url:
            result = await crawl_github_api(page, final_url, log_func)
            if result is not None:
                return result

        try:
            if log_func:
                log_func(f"浏览器: 正在加载页面...")
            await page.goto(final_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            err_msg = str(e)
            is_timeout = "Timeout" in err_msg or "timeout" in err_msg
            if log_func:
                if is_timeout:
                    log_func(f"浏览器: 加载页面超时 {final_url}")
                else:
                    log_func(f"浏览器: 加载页面失败 {final_url}: {e}")
            if is_timeout:
                return "[CRAWL_TIMEOUT]"
            return ""

        # Wait for content to stabilize
        prepend_text = ""
        try:
            if log_func:
                log_func(f"浏览器: 等待页面内容渲染...")
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

        if log_func:
            log_func(f"浏览器: 正在提取页面内容...")
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
        await release_page(page)
