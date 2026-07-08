import asyncio
import logging


logger = logging.getLogger(__name__)

# DOM-density content extraction script (runs in browser context via bridge.evaluate).
# 走 chrome.debugger 的 Runtime.evaluate,任意 JS(含 cloneNode/remove 等 DOM 变更)都允许。
_JS_EXTRACT_CONTENT = """(() => {
    // Guard against document.body being null (e.g., on redirects/error pages).
    const body = document.body;
    if (!body) return "";
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
    const clone = body.cloneNode(true);
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
        const rawHref = a.getAttribute('href');
        if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('javascript:')) return;
        const href = a.href || rawHref;
        const linkText = (a.innerText || '').trim();

        const isDownloadUrl = downloadExts.some(ext => (rawHref + ' ' + href).toLowerCase().includes(ext));
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
})()"""


async def extract_og_metadata(bridge, tab_id: int) -> dict:
    """Extract OpenGraph metadata from a page for better source previews."""
    try:
        return await bridge.evaluate(tab_id, r"""(() => {
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
        })()""", timeout_ms=10000)
    except Exception:
        return {}


async def extract_page_content(bridge, tab_id: int, url: str, log_func=None) -> str:
    """Extract main content from a page using DOM-density with retry logic.

    桥接走 Runtime.evaluate,执行上下文丢失时重试。无 page.wait_for_load_state,
    改用固定 sleep 给 DOM 恢复时间。
    """
    last_err = None
    for attempt in range(3):
        try:
            content = await bridge.evaluate(tab_id, _JS_EXTRACT_CONTENT, timeout_ms=30000)
            if isinstance(content, str):
                return content
            return ""
        except Exception as e:
            last_err = e
            msg = str(e)
            if "Execution context was destroyed" in msg or "Cannot find context" in msg or "No execution context" in msg:
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
            logger.error("Extraction error on %s: %s", url, e)
            break
    if last_err and log_func:
        log_func(f"浏览器: 内容提取失败 {url}: {last_err}")
    return ""
