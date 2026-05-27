import asyncio
import logging

from playwright.async_api import Page


logger = logging.getLogger(__name__)

# DOM-density content extraction script (runs in browser context).
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

    // Minimum content threshold - fall back to cleaned body
    if (text.replace(/\\s+/g,'').length < 200) {
        text = (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
    }

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

    if (collectedLinks.length > 0) {
        text += '\\n\\n--- 页面中的下载链接 ---';
        for (const link of collectedLinks) {
            text += '\\n[' + link.text + '](' + link.url + ')';
        }
    }

    return text;
}"""

_BLOCKED_RESOURCE_TYPES = {
    "image",
    "media",
    "font",
    "stylesheet",
    "websocket",
    "manifest",
    "texttrack",
}


async def extract_og_metadata(page: Page) -> dict:
    """Extract OpenGraph metadata from a page for better source previews."""
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


async def extract_page_content(page: Page, url: str) -> str:
    """Extract main content from a page using DOM-density with retry logic."""
    for attempt in range(3):
        try:
            content = await page.evaluate(_JS_EXTRACT_CONTENT)
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


async def install_resource_blocker(page: Page, should_block_url=None):
    """Abort requests for non-essential resources during content crawling."""
    async def _handle_route(route):
        request = route.request
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return

        if should_block_url:
            try:
                if should_block_url(request.url):
                    await route.abort()
                    return
            except Exception:
                await route.abort()
                return

        await route.continue_()

    await page.route("**/*", _handle_route)
