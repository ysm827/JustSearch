import urllib.parse


def clean_fallback_title(title: str, url: str = "") -> str:
    """Clean noisy multiline titles returned by search-result fallback parsing."""
    if not title:
        return ""

    lines = [
        line.strip()
        for line in title.replace("\r", "\n").split("\n")
        if line.strip()
    ]
    if not lines:
        return title.strip()
    if len(lines) == 1:
        return lines[0]

    hostname = ""
    try:
        hostname = urllib.parse.urlparse(url).hostname or ""
        hostname = hostname.removeprefix("www.")
    except Exception:
        pass

    def is_breadcrumb(line: str) -> bool:
        lower = line.lower()
        if hostname and hostname.lower() in lower:
            return True
        if "›" in line or ">" in line:
            return "." in line or "/" in line
        return bool(urllib.parse.urlparse(line).scheme)

    candidates = [line for line in lines if not is_breadcrumb(line)]
    if not candidates:
        candidates = lines

    return candidates[-1]


def is_generic_search_aux_title(title: str) -> bool:
    """Detect search-engine auxiliary links that are not real search results."""
    normalized = " ".join((title or "").split()).strip()
    if not normalized:
        return True

    lower = normalized.lower()
    return (
        normalized.startswith("更多关于") and normalized.endswith("的信息")
    ) or (
        lower.startswith("more about ") and lower.endswith(" information")
    )


def is_search_engine_internal_page(url: str) -> bool:
    """Return True for search pages that should not be crawled as sources."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    hostname = (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
    path = parsed.path or "/"
    query = urllib.parse.parse_qs(parsed.query)

    if hostname == "google.com":
        return path in {"/search", "/url"} or path.startswith("/sorry/")
    if hostname == "bing.com":
        return path in {"/search", "/ck/a"}
    if hostname == "duckduckgo.com":
        return ((path in {"/", "/html/", "/html"} and "q" in query) or path.startswith("/l/"))
    if hostname == "sogou.com":
        return path.startswith(("/web", "/link"))
    if hostname == "search.brave.com":
        return path == "/search"
    return False
