import asyncio
import base64
import ipaddress
import json
import logging
import os
import random
import re
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
        '.article-content, .post-body, #content, #main, .main-content, .page-content, ' +
        '.text-content, .detail-content, .news-content, .article-body, .post-text, ' +
        '#article-content, .article_detail, .content-body, .RichText, .rich_media_content, ' +
        '#js_content, .topic-richtext, .Post-RichTextContainer'
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

# PDF URL pattern
_PDF_PATTERN = re.compile(r'\.pdf(\?.*)?$', re.IGNORECASE)

# Maximum extracted content length (chars) to prevent memory bloat
_MAX_CONTENT_LENGTH = 200_000


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


async def _extract_og_metadata(page: Page) -> dict:
    """Extract OpenGraph metadata from page for better source previews."""
    try:
        return await page.evaluate(r"""() => {
            const getMeta = (name) => {
                const el = document.querySelector(`meta[property="${name}"]`) ||
                           document.querySelector(`meta[name="${name}"]`);
                return el ? el.content : '';
            };
            return {
                og_title: getMeta('og:title'),
                og_description: getMeta('og:description'),
                og_image: getMeta('og:image'),
                og_site_name: getMeta('og:site_name'),
                author: getMeta('author'),
                published_time: getMeta('article:published_time'),
            };
        }""")
    except Exception:
        return {}


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
    elements = await page.evaluate(r"""() => {
        const items = [];
        let idCounter = 0;

        function isVisible(elem) {
            if (!elem.getBoundingClientRect || !elem.checkVisibility) return false;
            const rect = elem.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && elem.checkVisibility();
        }

        const candidates = document.querySelectorAll('button, a[href], [role="button"]');

        // Blacklist patterns for elements that are never useful to click
        const blacklist = /^(home|login|sign in|sign up|menu|privacy|terms|登录|注册|分享|首页|关闭|评论|like|share|follow|subscribe|cookie|accept|dismiss|下载 app|open in app|get app|feedback|feedback|举报|投诉|more actions)$/i;
        // Generic navigation that rarely helps find content
        const navPatterns = /^(back|next|previous|prev|1|2|3|4|5|6|7|8|9|10|first|last|<|>|<<|>>)$/i;

        for (const el of candidates) {
            if (!isVisible(el)) continue;

            const text = el.innerText.trim();
            if (text.length < 2 || text.length > 50) continue;
            if (blacklist.test(text)) continue;
            if (navPatterns.test(text)) continue;

            // Skip elements in header/footer/nav sections
            const parent = el.closest('header, footer, nav, .navbar, .footer, .header, .sidebar, .nav-bar, #header, #footer, #nav');
            if (parent) continue;

            const rect = el.getBoundingClientRect();
            const tempId = "js-interact-" + idCounter++;

            items.push({
                id: tempId,
                text: text,
                tag: el.tagName.toLowerCase(),
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2
            });

            if (items.length >= 30) break;
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
                # Retry up to 3 times
                for attempt in range(3):
                    try:
                        await page.mouse.click(x, y)
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
            content = await page.evaluate(_JS_EXTRACT_CONTENT)
            # Truncate oversized content to prevent memory bloat
            if content and len(content) > _MAX_CONTENT_LENGTH:
                logger.warning("[Crawler] Content too large (%d chars), truncating: %s", len(content), url[:80])
                content = content[:_MAX_CONTENT_LENGTH] + "\n\n[... 内容过长，已截取]"
            return content
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
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet", "websocket", "manifest", "texttrack"}


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

    # PDF detection: skip browser crawling, return metadata
    if _PDF_PATTERN.search(final_url):
        if log_func:
            log_func(f"浏览器: 检测到 PDF 文件，跳过深度爬取")
        return f"[PDF 文档] {final_url}\n注意: PDF 文件无法直接提取内容，请访问链接查看原文。"

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
            await page.wait_for_load_state("networkidle", timeout=8000)
            # SPA / documentation sites often need extra rendering time
            spa_indicators = ['.wiki', '/docs/', '/documentation/', 'docusaurus', 'vitepress',
                             'notion.site', 'vercel.app', 'netlify.app',
                             'gitbook.io', 'readme.io', 'mkdocs']
            is_spa = any(ind in final_url.lower() for ind in spa_indicators)
            if is_spa:
                await asyncio.sleep(2.0)  # Extra wait for client-side rendering
                # Try to scroll to trigger lazy content
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                await asyncio.sleep(1.0)
            if "github.com" in final_url:
                if "tab=repositories" in final_url:
                    prepend_text = await extract_github_repo_stats(page, final_url, log_func) or ""
                elif "/stars" in final_url:
                    # GitHub stars page optimization
                    prepend_text = await extract_github_repo_stats(page, final_url, log_func) or ""
                elif "/blob/" not in final_url and "/tree/" not in final_url and "/issues/" not in final_url:
                    # GitHub repo homepage — try to extract README content specifically
                    try:
                        readme = await page.evaluate(r"""() => {
                            const readme = document.querySelector('[data-target="readme-toc.content"], article.markdown-body, .readme .markdown-body');
                            if (readme) {
                                // Remove anchor links from headings
                                readme.querySelectorAll('a.anchor').forEach(el => el.remove());
                                return readme.innerText.substring(0, 8000);
                            }
                            return null;
                        }""")
                        if readme and len(readme) > 200:
                            if log_func:
                                log_func(f"浏览器: GitHub README 提取成功 ({len(readme)} 字符)")
                            # Also get repo metadata
                            meta = await page.evaluate(r"""() => {
                                const parts = [];
                                const desc = document.querySelector('[data-testid="about-description"], .f4.my-3');
                                if (desc) parts.push('描述: ' + desc.innerText.trim());
                                const stars = document.querySelector('#repo-stars-counter-star, a[href$="/stargazers"]');
                                if (stars) parts.push('Stars: ' + stars.innerText.trim());
                                const forks = document.querySelector('#repo-network-counter, a[href$="/forks"]');
                                if (forks) parts.push('Forks: ' + forks.innerText.trim());
                                const lang = document.querySelector('[data-ga-click*="language"], ul.list-style-none li .color-fg-default');
                                if (lang) parts.push('语言: ' + lang.innerText.trim());
                                return parts.join('\n');
                            }""")
                            prepend_text = (meta + "\n\n--- README ---\n") if meta else "--- README ---\n"
                            prepend_text += readme + "\n--- END README ---\n\n"
                    except Exception:
                        pass
        except Exception:
            pass

        # Special handling for YouTube — extract video metadata and transcript
        if "youtube.com/watch" in final_url or "youtu.be/" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const parts = [];
                    const title = document.querySelector('h1 yt-formatted-string, h1');
                    if (title) parts.push('标题: ' + title.innerText);
                    const channel = document.querySelector('#channel-name a, ytd-channel-name a');
                    if (channel) parts.push('频道: ' + channel.innerText);
                    const views = document.querySelector('#info-container #count, .view-count');
                    if (views) parts.push(views.innerText);
                    const date = document.querySelector('#info-container #info-strings yt-formatted-string, #date');
                    if (date) parts.push('日期: ' + date.innerText);
                    const desc = document.querySelector('#description-inner, #attributed-snippet-text');
                    if (desc) parts.push('\n描述:\n' + desc.innerText.substring(0, 3000));
                    // Try to get transcript if expandable
                    const transcriptBtn = document.querySelector('[target-id="transcript"]');
                    if (transcriptBtn) parts.push('\n[有字幕/文字版可用]');
                    return parts.length > 0 ? parts.join('\n') : null;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: YouTube 视频信息提取成功")
                    return content
            except Exception:
                pass

        # Special handling for Bilibili — extract video metadata
        if "bilibili.com/video/" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const parts = [];
                    const title = document.querySelector('h1.video-info-title, h1');
                    if (title) parts.push('标题: ' + title.innerText);
                    const author = document.querySelector('.up-info__detail a.username, .up-name');
                    if (author) parts.push('UP主: ' + author.innerText);
                    const views = document.querySelector('.view-text, .video-data-list .view');
                    if (views) parts.push('播放: ' + views.innerText);
                    const desc = document.querySelector('.desc-info-text, .basic-desc-info');
                    if (desc) parts.push('\n简介:\n' + desc.innerText.substring(0, 3000));
                    return parts.length > 0 ? parts.join('\n') : null;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: Bilibili 视频信息提取成功")
                    return content
            except Exception:
                pass

        # Special handling for StackOverflow / StackExchange — extract Q&A
        if "stackoverflow.com" in final_url or "stackexchange.com" in final_url or "serverfault.com" in final_url or "superuser.com" in final_url or "askubuntu.com" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const parts = [];
                    const question = document.querySelector('.question .s-prose, .question .post-text');
                    if (question) parts.push('## 问题\n' + question.innerText.substring(0, 4000));
                    const answers = document.querySelectorAll('.answer .s-prose, .answer .post-text');
                    let answerText = '';
                    answers.forEach((a, i) => {
                        if (i < 3) answerText += '\n### 回答 ' + (i+1) + '\n' + a.innerText.substring(0, 4000) + '\n';
                    });
                    if (answerText) parts.push(answerText);
                    return parts.length > 0 ? parts.join('\n\n') : null;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: StackExchange 问答提取成功")
                    return content
            except Exception:
                pass

        # Special handling for Arxiv papers (abstract page only; HTML version uses default extraction)
        if "arxiv.org/abs/" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const parts = [];
                    const title = document.querySelector('h1.title');
                    if (title) parts.push('标题: ' + title.innerText.replace('Title:', '').trim());
                    const authors = document.querySelector('.authors');
                    if (authors) parts.push('作者: ' + authors.innerText.replace('Authors:', '').trim());
                    const abstract = document.querySelector('.abstract');
                    if (abstract) parts.push('\n摘要:\n' + abstract.innerText.replace('Abstract:', '').trim());
                    const date = document.querySelector('.dateline');
                    if (date) parts.push('日期: ' + date.innerText.trim());
                    return parts.length > 0 ? parts.join('\n') : null;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: Arxiv 论文信息提取成功")
                    return content
            except Exception:
                pass

        # Special handling for Zhihu — click "阅读全文" / "展开阅读全文" to expand
        if "zhihu.com" in final_url:
            try:
                expanded = await page.evaluate(r"""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const text = btn.innerText.trim();
                        if (text === '阅读全文' || text === '展开阅读全文' || text === '显示全部') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if expanded:
                    await asyncio.sleep(1.5)
                    if log_func:
                        log_func(f"浏览器: 知乎全文已展开")
            except Exception:
                pass

        # Special handling for Medium — remove paywall overlay
        if "medium.com" in final_url:
            try:
                await page.evaluate(r"""() => {
                    // Remove paywall overlays
                    document.querySelectorAll('[aria-label="Member-only story"], .metabar, .js-sticky-footer, .overlay').forEach(el => el.remove());
                    // Try to expand truncated content
                    const expandBtn = document.querySelector('button[data-action="expand"]');
                    if (expandBtn) expandBtn.click();
                }""")
            except Exception:
                pass

        # Special handling for WeChat articles — expand collapsed content
        if "mp.weixin.qq.com" in final_url:
            try:
                await page.evaluate(r"""() => {
                    const expandBtn = document.querySelector('#js_content_overflow_mask');
                    if (expandBtn) {
                        const clickEvent = new Event('click');
                        expandBtn.dispatchEvent(clickEvent);
                    }
                }""")
            except Exception:
                pass

        # Special handling for Baidu Baike — extract article content only
        if "baike.baidu.com" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const summary = document.querySelector('.lemma-summary, .lemma-desc');
                    const mainContent = document.querySelector('.main-content, .lemma-main-content, #J-lemma-content');
                    const parts = [];
                    if (summary) parts.push(summary.innerText);
                    if (mainContent) parts.push(mainContent.innerText);
                    return parts.length > 0 ? parts.join('\n\n') : null;
                }""")
                if content and len(content) > 200:
                    if log_func:
                        log_func(f"浏览器: 百度百科内容提取成功 ({len(content)} 字符)")
                    return content
            except Exception:
                pass

        # Special handling for Zhihu Zhuanlan — expand full article
        if "zhuanlan.zhihu.com" in final_url:
            try:
                await page.evaluate(r"""() => {
                    const btn = document.querySelector('.ContentItem-expandButton');
                    if (btn) btn.click();
                }""")
                await asyncio.sleep(1.0)
            except Exception:
                pass

        # Special handling for Toutiao articles — extract article body
        if "toutiao.com/article/" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const article = document.querySelector('.article-content, .syl-article-base, #article-root');
                    if (article) return article.innerText;
                    return null;
                }""")
                if content and len(content) > 200:
                    if log_func:
                        log_func(f"浏览器: 头条文章内容提取成功 ({len(content)} 字符)")
                    return content
            except Exception:
                pass

        # Special handling for CSDN — remove ads and recommendations
        if "csdn.net" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const article = document.querySelector('#article_content, #content_views');
                    if (article) {
                        const clone = article.cloneNode(true);
                        clone.querySelectorAll('.hide-article-box, .more-toolbox, .recommend-box, .person-messagebox, script, style').forEach(el => el.remove());
                        return clone.innerText;
                    }
                    return null;
                }""")
                if content and len(content) > 200:
                    if log_func:
                        log_func(f"浏览器: CSDN 文章内容提取成功 ({len(content)} 字符)")
                    return content
            except Exception:
                pass

        # Special handling for Juejin (掘金) — extract article body
        if "juejin.cn" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const article = document.querySelector('.article-content, .markdown-body');
                    if (article) return article.innerText;
                    return null;
                }""")
                if content and len(content) > 200:
                    if log_func:
                        log_func(f"浏览器: 掘金文章内容提取成功 ({len(content)} 字符)")
                    return content
            except Exception:
                pass

        # Special handling for Wikipedia — extract main article content only
        if "wikipedia.org/wiki/" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const article = document.querySelector('#mw-content-text .mw-parser-output');
                    if (!article) return null;
                    const clone = article.cloneNode(true);
                    clone.querySelectorAll('.reference, .noprint, .mw-editsection, .sidebar, .navbox, .infobox, table, .toc').forEach(el => el.remove());
                    return clone.innerText;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: Wikipedia 内容提取成功")
                    return content
            except Exception:
                pass  # Fall through to default extraction

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

        # Extract OpenGraph metadata for better context
        og = await _extract_og_metadata(page)
        if og and any(og.values()):
            meta_lines = []
            if og.get('og_description'):
                meta_lines.append(f"页面描述: {og['og_description']}")
            if og.get('author'):
                meta_lines.append(f"作者: {og['author']}")
            if og.get('published_time'):
                meta_lines.append(f"发布时间: {og['published_time']}")
            if meta_lines:
                content = "[元信息] " + " | ".join(meta_lines) + "\n\n" + content

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
