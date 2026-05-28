import asyncio
import json
import logging
import os
import random
import re

from playwright.async_api import Page
from playwright_stealth import Stealth

from .browser_context import get_new_page, release_page, get_context_pool_status
from .crawler.content import (
    extract_og_metadata,
    extract_page_content,
    install_resource_blocker,
)
from .crawler.redirects import resolve_redirect_url
from .crawler.security import is_private_url
from .interaction import register_interaction_session, remove_interaction_session

logger = logging.getLogger(__name__)

# PDF URL pattern
_PDF_PATTERN = re.compile(r'\.pdf(\?.*)?$', re.IGNORECASE)


def _format_pdf_metadata(url: str) -> str:
    return f"[PDF 文档] {url}\n注意: PDF 文件无法直接提取内容，请访问链接查看原文。"


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
        return _format_pdf_metadata(final_url)

    page = await get_new_page()
    await stealth.apply_stealth_async(page)
    # Block non-essential resources to speed up crawling
    await install_resource_blocker(page, should_block_url=is_private_url)

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
            response = await page.goto(final_url, wait_until="domcontentloaded", timeout=20000)
            navigated_url = (
                getattr(page, "url", "")
                or getattr(response, "url", "")
                or final_url
            )
            if navigated_url != final_url:
                if is_private_url(navigated_url):
                    if log_func:
                        log_func(f"浏览器: 拒绝访问跳转后的内网地址 {navigated_url}")
                    return "错误: 不允许访问内网地址"
                final_url = navigated_url
                if _PDF_PATTERN.search(final_url):
                    if log_func:
                        log_func(f"浏览器: 跳转到 PDF 文件，跳过深度爬取")
                    return _format_pdf_metadata(final_url)
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

        # Special handling for Xiaohongshu (小红书) — extract note content
        if "xiaohongshu.com/explore/" in final_url or "xiaohongshu.com/discovery/item/" in final_url or "xhslink.com" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const parts = [];
                    const title = document.querySelector('#detail-title, .note-content .title');
                    if (title) parts.push('标题: ' + title.innerText.trim());
                    const author = document.querySelector('.author-wrapper .username, .user-nickname');
                    if (author) parts.push('作者: ' + author.innerText.trim());
                    const desc = document.querySelector('#detail-desc, .note-text, .desc');
                    if (desc) parts.push('\n简介:\n' + desc.innerText.trim().substring(0, 5000));
                    const tags = document.querySelectorAll('.tag, .hash-tag');
                    if (tags.length > 0) {
                        const tagText = Array.from(tags).map(t => t.innerText.trim()).join(', ');
                        if (tagText) parts.push('\n标签: ' + tagText);
                    }
                    return parts.length > 0 ? parts.join('\n') : null;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: 小红书笔记内容提取成功")
                    return content
            except Exception:
                pass

        # Special handling for GitHub — extract README, issues, or PR content
        if "github.com" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    // README on repo page
                    const readme = document.querySelector('#readme article, .markdown-body');
                    if (readme) {
                        const repoName = document.querySelector('strong a, .js-repo-nav span');
                        let result = '';
                        if (repoName) result += 'Repo: ' + repoName.innerText.trim() + '\n\n';
                        result += readme.innerText.trim();
                        // Also get star count
                        const stars = document.querySelector('#repo-stars-counter-star, a[href$="/stargazers"]');
                        if (stars) result += '\n\nStars: ' + stars.innerText.trim();
                        return result.substring(0, 10000);
                    }
                    // Issue/PR page
                    const issueBody = document.querySelector('.js-discussion, .comment-body');
                    if (issueBody) {
                        const title = document.querySelector('.js-issue-title, .gh-header-title');
                        let result = '';
                        if (title) result += 'Title: ' + title.innerText.trim() + '\n\n';
                        result += issueBody.innerText.trim();
                        return result.substring(0, 8000);
                    }
                    return null;
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: GitHub 页面内容提取成功")
                    return content
            except Exception:
                pass

        # Special handling for Bilibili (B站) — extract video info and comments
        if "bilibili.com/video/" in final_url or "b23.tv/" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const parts = [];
                    const title = document.querySelector('h1.video-title, .video-info-container .video-title');
                    if (title) parts.push('标题: ' + title.innerText.trim());
                    const author = document.querySelector('.up-info .username, .up-name');
                    if (author) parts.push('UP主: ' + author.innerText.trim());
                    const views = document.querySelector('.view-text, .video-data .view');
                    if (views) parts.push('播放量: ' + views.innerText.trim());
                    const date = document.querySelector('.pubdate-ip-text, .pubdate-text');
                    if (date) parts.push('发布时间: ' + date.innerText.trim());
                    const desc = document.querySelector('.desc-info-text, .basic-desc-info');
                    if (desc) parts.push('\n简介: ' + desc.innerText.trim().substring(0, 2000));
                    const tags = document.querySelectorAll('.tag-link, .tag-area a');
                    if (tags.length > 0) {
                        const tagText = Array.from(tags).map(t => t.innerText.trim()).filter(t => t).join(', ');
                        if (tagText) parts.push('\n标签: ' + tagText);
                    }
                    // Try to get Danmaku/comment summary
                    const comments = document.querySelectorAll('.reply-content .root-reply .reply-content-container .reply-content');
                    if (comments.length > 0) {
                        parts.push('\n热门评论:');
                        const maxComments = Math.min(comments.length, 5);
                        for (let i = 0; i < maxComments; i++) {
                            parts.push('  ' + (i+1) + '. ' + comments[i].innerText.trim().substring(0, 200));
                        }
                    }
                    return parts.length > 0 ? parts.join('\n') : null;
                }""")
                if content:
                    if log_func:
                        log_func("浏览器: B站视频信息提取成功")
                    return content
            except Exception:
                pass

        # Special handling for WeChat Official Account articles (微信公众号)
        if "mp.weixin.qq.com" in final_url:
            try:
                content = await page.evaluate(r"""() => {
                    const title = document.querySelector('#activity-name, .rich_media_title');
                    const author = document.querySelector('#js_name, .rich_media_meta_nickname a');
                    const date = document.querySelector('#publish_time, .rich_media_meta_primary_category');
                    const body = document.querySelector('#js_content, .rich_media_content');
                    if (!body) return null;
                    let result = '';
                    if (title) result += '标题: ' + title.innerText.trim() + '\n';
                    if (author) result += '公众号: ' + author.innerText.trim() + '\n';
                    if (date) result += '时间: ' + date.innerText.trim() + '\n';
                    result += '\n' + body.innerText.trim();
                    return result.substring(0, 10000);
                }""")
                if content:
                    if log_func:
                        log_func(f"浏览器: 微信公众号文章提取成功")
                    return content
            except Exception:
                pass

        # Detect Cloudflare challenge page
        is_cf_challenge = await page.evaluate(r"""() => {
            const title = document.title || '';
            const body = document.body ? document.body.innerText : '';
            return title.includes('Just a moment') ||
                   title.includes('Attention Required') ||
                   body.includes('Checking your browser') ||
                   body.includes('cf-browser-verification') ||
                   document.querySelector('#challenge-running, .challenge-running') !== null;
        }""")
        if is_cf_challenge:
            if log_func:
                log_func("浏览器: 检测到 Cloudflare 验证页面，等待通过...")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            # Re-check after waiting
            still_blocked = await page.evaluate(r"""() => {
                const title = document.title || '';
                return title.includes('Just a moment') || title.includes('Attention Required');
            }""")
            if still_blocked:
                if log_func:
                    log_func("浏览器: Cloudflare 验证未通过，跳过此页面")
                return None

        # Interactive Mode
        if interactive_mode and query and llm_client:
            try:
                await run_interactive_mode(page, query, llm_client, log_func)
            except Exception as e:
                if log_func:
                    log_func(f"浏览器: 交互模式执行出错: {e}")

        if log_func:
            log_func(f"浏览器: 正在提取页面内容...")
        content = await extract_page_content(page, final_url)

        # Extract OpenGraph metadata for better context
        og = await extract_og_metadata(page)
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
