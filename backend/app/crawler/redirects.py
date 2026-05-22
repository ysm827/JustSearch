import base64
import html
import re
import urllib.parse


async def resolve_redirect_url(url: str, log_func=None) -> str:
    """Resolve search-engine redirect URLs to their final target URLs."""
    final_url = url
    if (
        "bing.com/ck/a" not in url
        and "google.com/url" not in url
        and "duckduckgo.com/l/" not in url
        and not _is_sogou_link_url(url)
    ):
        return final_url

    if log_func:
        log_func("浏览器: 检测到重定向 URL，正在尝试提取目标...")

    if "duckduckgo.com/l/" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params:
                final_url = params["uddg"][0]
                if log_func:
                    log_func(f"浏览器: 提取 DuckDuckGo 重定向 URL 成功: {final_url}")
        except Exception as e:
            if log_func:
                log_func(f"浏览器: 提取 DuckDuckGo 重定向 URL 失败: {e}")

    elif "bing.com/ck/a" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "u" in params:
            u_val = params["u"][0]
            if u_val.startswith("a1"):
                try:
                    b64_part = u_val[2:]
                    b64_part += "=" * ((4 - len(b64_part) % 4) % 4)
                    final_url = base64.b64decode(b64_part).decode("utf-8")
                    if log_func:
                        log_func(f"浏览器: 提取 Bing 重定向 URL 成功: {final_url}")
                except Exception as e:
                    if log_func:
                        log_func(f"浏览器: 提取 Bing 重定向 URL 失败: {e}")

    elif _is_sogou_link_url(url):
        try:
            html_text = await _fetch_sogou_redirect_html(url)
            extracted_url = _extract_html_redirect_url(html_text)
            if extracted_url:
                final_url = extracted_url
                if log_func:
                    log_func(f"浏览器: 提取 Sogou 重定向 URL 成功: {final_url}")
        except Exception as e:
            if log_func:
                log_func(f"浏览器: 提取 Sogou 重定向 URL 失败: {e}")

    return final_url


def _is_sogou_link_url(url: str) -> bool:
    """Return True for Sogou result-wrapper URLs."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    hostname = parsed.hostname or ""
    return hostname.endswith("sogou.com") and parsed.path.startswith("/link")


async def _fetch_sogou_redirect_html(url: str) -> str:
    """Fetch the lightweight Sogou wrapper page used to hold JS redirects."""
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=10.0,
        headers={"User-Agent": "JustSearch/1.0"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _extract_html_redirect_url(html_text: str) -> str:
    """Extract a target URL from script/meta HTML redirects."""
    if not html_text:
        return ""

    patterns = [
        r"""window\.location(?:\.replace)?\(\s*["']([^"']+)["']\s*\)""",
        r"""location\.href\s*=\s*["']([^"']+)["']""",
        r"""http-equiv=["']refresh["'][^>]+content=["'][^"']*url=([^"']+)["']""",
        r"""content=["'][^"']*url=([^"']+)["'][^>]+http-equiv=["']refresh["']""",
    ]

    for pattern in patterns:
        match = re.search(pattern, html_text, re.IGNORECASE)
        if not match:
            continue
        candidate = html.unescape(match.group(1)).strip(" '\"")
        if candidate.startswith(("http://", "https://")):
            return candidate
    return ""
