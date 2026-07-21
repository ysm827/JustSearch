import copy
import json
import hashlib
import logging
import os
import re
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any

from .openai_client import create_openai_client
from .prompts import (
    ANSWER_GENERATION_PROMPT,
    ANSWER_GENERATION_LIVE_ARTIFACTS_PROMPT,
    select_live_artifacts_protocol,
    CLICK_DECISION_PROMPT,
    RELEVANCE_ASSESSMENT_PROMPT,
    TASK_ANALYSIS_PROMPT,
    CITATION_VERIFICATION_PROMPT,
)

logger = logging.getLogger(__name__)

# LLM 调用超时
_LLM_TIMEOUT = 120  # 秒（默认，用于 generate_answer）
_LLM_SHORT_TIMEOUT = 30  # 秒（用于 analyze_task / assess_relevance 等短操作）
_LLM_CONNECT_TIMEOUT = 20.0  # 秒：建连超时，避免网关假死拖很久
_GENERATE_ANSWER_RETRIES = 4  # 流式生成答案的重试次数（含首次）

# 并发 LLM 请求限制
_LLM_CONCURRENCY = asyncio.Semaphore(5)

# OpenAI SDK / httpx 网络层可重试错误名与关键词
_RETRYABLE_ERROR_TYPES = (
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "RemoteProtocolError",
    "ConnectError",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ConnectTimeout",
)
_RETRYABLE_ERROR_MARKERS = (
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "network is unreachable",
    "name or service not known",
    "temporary failure in name resolution",
    "server disconnected",
    "broken pipe",
    "ssl",
    "eof occurred",
    "remote end closed",
    "502",
    "503",
    "504",
)

# Task analysis cache — avoids duplicate API calls for identical queries
_ANALYSIS_CACHE: dict = {}
_ANALYSIS_CACHE_MAX = 50
_ANALYSIS_CACHE_TTL = 180  # 3 minutes

_INLINE_HTML_ROOT_RE = re.compile(
    r"^<(?:article|aside|blockquote|button|details|div|figure|footer|form|h[1-6]|header|main|nav|ol|p|section|span|table|ul)\b[\s\S]*</(?:article|aside|blockquote|button|details|div|figure|footer|form|h[1-6]|header|main|nav|ol|p|section|span|table|ul)>$",
    re.IGNORECASE,
)
_HTML_FENCE_RE = re.compile(
    r"^```(?:amc-live-artifact-html|html|svg)?\s*\n([\s\S]*?)\n?```\s*$",
    re.IGNORECASE,
)
_STREAMABLE_LIVE_ARTIFACT_FENCE_RE = re.compile(
    r"^```(?:amc-live-artifact-html|html|svg)(?:\s|$)",
    re.IGNORECASE,
)
_FULL_HTML_DOCUMENT_RE = re.compile(
    r"^(?:<!doctype\s+html\b[^>]*>\s*)?<html\b[\s\S]*</html>$",
    re.IGNORECASE,
)
_SVG_DOCUMENT_RE = re.compile(
    r"^<svg\b[\s\S]*</svg>$",
    re.IGNORECASE,
)
# Models (esp. ZH) often emit 状态/缺失信息/回答 with half- or full-width colons.
_ANSWER_FIELD_STATUS_RE = re.compile(
    r"(?:^|\n)\s*(?:Status|状态)\s*[:：]\s*([^\n]*)",
    re.IGNORECASE,
)
_ANSWER_FIELD_MISSING_RE = re.compile(
    r"(?:^|\n)\s*(?:Missing_Info|Missing Info|缺失信息)\s*[:：]\s*",
    re.IGNORECASE,
)
_ANSWER_FIELD_ANSWER_RE = re.compile(
    r"(?:^|\n)\s*(?:Answer|回答)\s*[:：]\s*",
    re.IGNORECASE,
)
_EMBEDDED_HTML_START_RE = re.compile(
    r"<(?:article|aside|details|div|figure|footer|header|main|nav|ol|section|table|ul|svg)\b",
    re.IGNORECASE,
)
_LIVE_ARTIFACT_ROOT_STYLE = (
    "display:block;width:100%;box-sizing:border-box;max-width:100%;"
    "overflow-wrap:anywhere;background:transparent;"
)


class LLMProviderConfigurationError(RuntimeError):
    """Raised when the configured model provider cannot serve requests."""


def _provider_error_message(error: Exception) -> str:
    status_code = getattr(error, "status_code", 0) or 0
    err_str = str(error)
    err_lower = err_str.lower()

    if status_code == 401 or "unauthorized" in err_lower:
        return "模型服务返回 401：API 密钥无效或已过期，请检查设置中的 API Key。"
    if status_code == 402 or "insufficient" in err_lower or "quota" in err_lower:
        return "模型服务返回 402：账户额度不足或已欠费，请检查模型服务账户余额。"
    if (
        status_code == 403
        or "subscription_not_found" in err_lower
        or "no active subscription" in err_lower
        or "forbidden" in err_lower
    ):
        return "模型服务返回 403：当前 API Key 所属账户没有可用订阅，请在模型服务后台开通/续订后重试。"
    if status_code == 404 or "model" in err_lower and "not found" in err_lower:
        return "模型服务返回 404：模型不存在或当前账户无权访问，请检查 Model ID。"
    return ""


def _status_code_of(error: BaseException) -> int:
    code = getattr(error, "status_code", None)
    if isinstance(code, int) and code > 0:
        return code
    response = getattr(error, "response", None)
    if response is not None:
        resp_code = getattr(response, "status_code", None)
        if isinstance(resp_code, int) and resp_code > 0:
            return resp_code
    return 0


def _is_retryable_llm_error(error: BaseException) -> bool:
    """Network blips, rate limits, and gateway 5xx should be retried."""
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True

    status_code = _status_code_of(error)
    if status_code in (408, 409, 425, 429, 500, 502, 503, 504):
        return True

    type_name = type(error).__name__
    if type_name in _RETRYABLE_ERROR_TYPES:
        return True

    # Walk causes (SDK often wraps httpx errors).
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        if name in _RETRYABLE_ERROR_TYPES:
            return True
        text = str(current).lower()
        if any(marker in text for marker in _RETRYABLE_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _retry_backoff_seconds(attempt: int, *, base: float = 1.5, cap: float = 20.0) -> float:
    """Exponential backoff with jitter. attempt is 0-based."""
    import random as _rand

    delay = min(cap, (2 ** attempt) * base) + _rand.uniform(0, 1.0)
    return max(0.5, delay)


def _strip_live_artifact_fence(answer: str) -> str:
    text = (answer or "").strip()
    match = _HTML_FENCE_RE.match(text)
    return match.group(1).strip() if match else text


def _looks_like_inline_live_artifact(answer: str) -> bool:
    text = _strip_live_artifact_fence(answer)
    if not text:
        return False
    if _FULL_HTML_DOCUMENT_RE.match(text) or _SVG_DOCUMENT_RE.match(text):
        return True
    if re.search(r"<!doctype|<html\b|<head\b|<body\b", text, re.IGNORECASE):
        return False
    return bool(_INLINE_HTML_ROOT_RE.match(text))


def _is_streamable_live_artifact_answer(answer: str) -> bool:
    """Return True once a live-artifact answer is clearly raw HTML/SVG."""
    text = (answer or "").lstrip()
    return text.startswith("<") or bool(_STREAMABLE_LIVE_ARTIFACT_FENCE_RE.match(text))


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _inline_format_text(text: str) -> str:
    escaped = _escape_html(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _markdown_to_live_artifact_html(answer: str) -> str:
    lines = [line.rstrip() for line in (answer or "").strip().splitlines()]
    html_parts = [
        '<section style="display:block;width:100%;box-sizing:border-box;max-width:100%;overflow-wrap:anywhere;">'
    ]
    list_open = False

    def close_list():
        nonlocal list_open
        if list_open:
            html_parts.append("</ul>")
            list_open = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            close_list()
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            close_list()
            level = "h2" if len(heading.group(1)) <= 2 else "h3"
            html_parts.append(f"<{level}>{_inline_format_text(heading.group(2))}</{level}>")
            continue

        bullet = re.match(r"^(?:[-*]|\d+[.)])\s+(.+)$", line)
        if bullet:
            if not list_open:
                html_parts.append('<ul style="margin:0.5rem 0 0.75rem 1.1rem;padding:0;">')
                list_open = True
            html_parts.append(f"<li>{_inline_format_text(bullet.group(1))}</li>")
            continue

        close_list()
        html_parts.append(f"<p>{_inline_format_text(line)}</p>")

    close_list()
    html_parts.append("</section>")
    return "".join(html_parts)


def _parse_answer_envelope(text: str) -> Dict[str, Any]:
    """Split Status / Missing_Info / Answer envelopes (EN or ZH labels).

    Returns keys: status, missing_info, answer, had_envelope.
    When no Answer/回答 marker is present, ``answer`` is the original text.
    """
    raw = text or ""
    status = "sufficient"
    missing_info = ""
    had_envelope = False

    status_match = _ANSWER_FIELD_STATUS_RE.search(raw)
    if status_match:
        had_envelope = True
        status_value = (status_match.group(1) or "").strip().lower()
        if (
            "insufficient" in status_value
            or status_value in {"不足", "不充分", "不够", "不完整"}
            or status_value.startswith("不足")
        ):
            status = "insufficient"

    missing_match = _ANSWER_FIELD_MISSING_RE.search(raw)
    answer_match = _ANSWER_FIELD_ANSWER_RE.search(raw)

    if missing_match:
        had_envelope = True
        miss_start = missing_match.end()
        miss_end = answer_match.start() if answer_match else len(raw)
        missing_info = raw[miss_start:miss_end].strip()

    if answer_match:
        had_envelope = True
        answer = raw[answer_match.end():].strip()
    else:
        answer = raw.strip()
        # Drop bare status/missing header lines when Answer marker is missing.
        if status_match or missing_match:
            lines = []
            for line in answer.split("\n"):
                if _ANSWER_FIELD_STATUS_RE.match("\n" + line) or _ANSWER_FIELD_STATUS_RE.match(line):
                    continue
                if re.match(r"^\s*(?:Status|状态)\s*[:：]", line, re.IGNORECASE):
                    continue
                if re.match(
                    r"^\s*(?:Missing_Info|Missing Info|缺失信息)\s*[:：]",
                    line,
                    re.IGNORECASE,
                ):
                    continue
                lines.append(line)
            answer = "\n".join(lines).strip()

    return {
        "status": status,
        "missing_info": missing_info,
        "answer": answer,
        "had_envelope": had_envelope,
    }


def _split_prose_and_html_artifact(text: str) -> Optional[tuple[str, str]]:
    """If prose is followed by a substantial HTML fragment, return (prose, html)."""
    raw = text or ""
    match = _EMBEDDED_HTML_START_RE.search(raw)
    if not match:
        return None
    # Prefer starting at a line boundary so we don't split mid-token.
    start = match.start()
    line_start = raw.rfind("\n", 0, start) + 1
    # Only rewind to line start when that line is mostly the tag (no long prose).
    prefix_on_line = raw[line_start:start]
    if prefix_on_line.strip() == "":
        start = line_start

    prose = raw[:start].strip()
    html = raw[start:].strip()
    if not html.startswith("<"):
        return None
    tag_count = len(re.findall(r"</?[a-zA-Z][a-zA-Z0-9:-]*\b", html))
    if tag_count < 2:
        return None
    # Avoid treating a single stray tag inside markdown as an artifact body.
    if not prose and not _looks_like_inline_live_artifact(html) and tag_count < 4:
        return None
    return prose, html


def _wrap_live_artifact_root(*parts: str) -> str:
    inner = "".join(part for part in parts if part)
    return f'<div style="{_LIVE_ARTIFACT_ROOT_STYLE}">{inner}</div>'


def ensure_live_artifact_answer(answer: str) -> str:
    """Return an inline Live Artifact even when a model falls back to Markdown.

    Critical: never HTML-escape an already-produced artifact. Partial answers often
    arrive as prose/warning + raw HTML, or as a ZH/EN Status envelope around HTML.
    Escaping those tags makes the chat show source markup (the bug in multi-turn
    insufficient follow-ups).
    """
    stripped = _strip_live_artifact_fence(answer)
    if not stripped:
        return ""

    envelope = _parse_answer_envelope(stripped)
    body = envelope["answer"] if envelope["had_envelope"] else stripped
    body = _strip_live_artifact_fence(body).strip()
    if not body:
        return ""

    if _looks_like_inline_live_artifact(body):
        return body

    split = _split_prose_and_html_artifact(body)
    if split is not None:
        prose, html = split
        html = html.strip()
        if _looks_like_inline_live_artifact(html) or html.lstrip().startswith("<"):
            if not prose:
                return html if _looks_like_inline_live_artifact(html) else _wrap_live_artifact_root(html)
            # Keep prose readable without destroying the HTML artifact.
            prose_html = _markdown_to_live_artifact_html(prose)
            # _markdown_to_live_artifact_html already wraps in <section>; nest both.
            return _wrap_live_artifact_root(prose_html, html)

    return _markdown_to_live_artifact_html(body)


def _clone_cached_analysis_result(result: Any) -> Any:
    """Return an isolated copy so callers cannot mutate cached LLM analysis."""
    return copy.deepcopy(result)


def _cache_analysis_result(key: str, result: Any):
    """Store analysis result in cache, evicting old entries if needed."""
    import time
    if len(_ANALYSIS_CACHE) >= _ANALYSIS_CACHE_MAX:
        # Evict oldest entries
        sorted_keys = sorted(_ANALYSIS_CACHE.keys(), key=lambda k: _ANALYSIS_CACHE[k][1])
        for k in sorted_keys[:_ANALYSIS_CACHE_MAX // 2]:
            del _ANALYSIS_CACHE[k]
    _ANALYSIS_CACHE[key] = (_clone_cached_analysis_result(result), time.time())


def _cache_digest(value: Any) -> str:
    """Create a stable digest for cache inputs without keeping large keys in memory."""
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _history_cache_digest(history: Optional[List[Dict[str, str]]]) -> str:
    if not history:
        return "no-history"
    normalized = [
        {
            "role": str(msg.get("role", "user")),
            "content": str(msg.get("content") or ""),
        }
        for msg in history
        if isinstance(msg, dict)
    ]
    return _cache_digest(normalized)


def _snippet_cache_digest(snippets: List[Dict]) -> str:
    normalized = [
        {
            "id": item.get("id"),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("snippet", ""),
            "date": item.get("date", ""),
        }
        for item in snippets
        if isinstance(item, dict)
    ]
    return _cache_digest(normalized)


def _normalize_text_list(value: Any, *, max_items: int | None = None) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []

    items = []
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        items.append(text)
        if max_items and len(items) >= max_items:
            break
    return items


def _normalize_int_list(value: Any, *, max_items: int | None = None) -> list[int]:
    if isinstance(value, str):
        raw_items = re.split(r"[\s,，]+", value.strip())
    elif isinstance(value, list):
        raw_items = value
    else:
        return []

    items = []
    for item in raw_items:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        items.append(parsed)
        if max_items and len(items) >= max_items:
            break
    return items


def _coerce_bool(value: Any) -> bool:
    """Coerce JSON-ish booleans without treating non-empty "false" as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")
# A genuinely-structured HTML tag (named opening/closing tag), not the "<" / ">" of
# prose like "2 < x > 10" or a code token like "<vector>".
_HTML_TAG_STRUCTURE_RE = re.compile(r"</?[a-zA-Z][\w\-]*(\s[^>]*)?/?>")
# Short follow-ups that almost always need prior-turn entities.
_FOLLOWUP_HINT_RE = re.compile(
    r"(具体时间|几点|国内时间|北京时间|当地时间|那他|那她|那它|那个|这个|"
    r"英文版|中文版|详细说说|详细一点|再说说|还有呢|然后呢|为什么|"
    r"what\s+about|how\s+about|when\s+exactly|local\s+time|beijing\s+time|"
    r"tell\s+me\s+more|more\s+details|and\s+the\s+time)",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "的", "了", "是", "在", "和", "与", "或", "及", "等", "对", "为", "有", "也", "就",
        "都", "而", "被", "把", "从", "到", "中", "上", "下", "一个", "什么", "怎么", "如何",
        "哪些", "这个", "那个", "时候", "时间", "具体", "国内", "问题", "回答", "根据",
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was",
        "were", "be", "been", "it", "this", "that", "with", "from", "by", "as", "at",
        "what", "when", "where", "who", "how", "why", "about", "more", "next", "time",
    }
)


def _strip_html_to_text(text: str) -> str:
    """Remove HTML markup for compact conversation context."""
    cleaned = _HTML_TAG_RE.sub(" ", text or "")
    cleaned = cleaned.replace("&nbsp;", " ").replace("&amp;", "&")
    cleaned = cleaned.replace("&lt;", "<").replace("&gt;", ">")
    cleaned = cleaned.replace("&quot;", '"').replace("&#39;", "'")
    return _MULTI_SPACE_RE.sub(" ", cleaned).strip()


def _summarize_message_content(role: str, content: str, *, max_chars: int = 900) -> str:
    """Compress history content for model context (esp. Live Artifact HTML)."""
    text = str(content or "").strip()
    if not text:
        return ""
    # Only assistant turns are expected to contain rendered artifacts. Preserve user
    # angle-bracket code/types such as <vector>, <Map<K,V>>, and comparisons.
    if role == "assistant" and "<" in text and ">" in text and (
        _looks_like_inline_live_artifact(text) or bool(_HTML_TAG_STRUCTURE_RE.search(text))
    ):
        text = _strip_html_to_text(text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    if len(text) <= max_chars:
        return text
    # Prefer head + brief tail so conclusions and closing notes survive.
    head = max_chars - 80
    if head < 200:
        return text[: max_chars - 1] + "…"
    return text[:head].rstrip() + " … " + text[-60:].lstrip()


def _extract_history_anchor_terms(history: Optional[List[Dict[str, str]]], *, max_terms: int = 8) -> list[str]:
    """Heuristic entity/topic anchors from recent turns for rewrite fallback."""
    if not history:
        return []
    recent = [msg for msg in history if isinstance(msg, dict)][-6:]
    texts: list[str] = []
    for msg in recent:
        role = str(msg.get("role", "user"))
        content = _summarize_message_content(role, str(msg.get("content") or ""), max_chars=400)
        if content:
            texts.append(content)
    blob = " ".join(texts)
    if not blob:
        return []

    candidates: list[str] = []
    # Prefer multi-char CJK runs and capitalized English tokens / alphanumerics.
    # CJK cap raised so long proper nouns (e.g. org names) stay as one anchor.
    # English terms accept a single leading letter (so C / R language names are
    # not lost) and strip trailing punctuation (Python. -> Python) below.
    for match in re.finditer(r"[\u4e00-\u9fff]{2,30}|[A-Za-z][A-Za-z0-9\-+.]*", blob):
        term = match.group(0).strip().rstrip(".-+")
        if not term or term.lower() in _STOPWORDS or term in _STOPWORDS:
            continue
        if term not in candidates:
            candidates.append(term)
        if len(candidates) >= max_terms * 2:
            break

    # Boost terms that appear in the latest user turn.
    last_user = ""
    for msg in reversed(recent):
        if msg.get("role") == "user":
            last_user = _summarize_message_content("user", str(msg.get("content") or ""), max_chars=200)
            break
    ranked = sorted(
        candidates,
        key=lambda t: (0 if t in last_user else 1, -len(t), candidates.index(t)),
    )
    return ranked[:max_terms]


def _looks_like_followup_query(user_input: str, history: Optional[List[Dict[str, str]]]) -> bool:
    text = (user_input or "").strip()
    if not text or not history:
        return False
    if _FOLLOWUP_HINT_RE.search(text):
        return True
    # Very short questions without clear named entities often depend on context.
    if len(text) <= 24 and ("?" in text or "？" in text or text.endswith(("呢", "吗", "嘛"))):
        return True
    # Pronoun-heavy English stubs
    if re.fullmatch(r"(and\s+)?(the\s+)?(time|date|score|price|second|first|next)(\s+one)?\??", text, re.I):
        return True
    return False


def _queries_need_history_anchors(queries: list[str], entities: list[str], user_input: str) -> bool:
    """True when search queries look too thin to stand alone as follow-ups."""
    if not queries:
        return True
    joined = " ".join(queries)
    if entities and not any(e and e in joined for e in entities):
        # Model produced entities but forgot them in queries — force repair.
        return True
    # If every query is barely longer than the raw follow-up, it is likely unresolved.
    raw = (user_input or "").strip()
    if raw and all(len(q) <= max(len(raw) + 6, 18) for q in queries):
        return True
    return False


def _build_search_analysis_result(
    *,
    queries: list[str],
    resolved_query: str = "",
    entities: Optional[list[str]] = None,
    is_followup: bool = False,
    topic_changed: bool = False,
    user_input: str = "",
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Normalize task-analysis search payload; repair thin follow-up queries."""
    clean_queries = _normalize_text_list(queries, max_items=3)
    clean_entities = _normalize_text_list(entities or [], max_items=8)
    followup = bool(is_followup) or _looks_like_followup_query(user_input, history)
    resolved = (resolved_query or "").strip()

    if followup and history:
        anchors = clean_entities or _extract_history_anchor_terms(history)
        if not clean_entities:
            clean_entities = anchors
        if not resolved or (anchors and not any(a in resolved for a in anchors[:3])):
            base = resolved or (user_input or "").strip()
            if anchors:
                resolved = f"{' '.join(anchors[:4])} {base}".strip()
            else:
                # Fall back to last user question as topical glue.
                last_user = ""
                for msg in reversed(history):
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        last_user = _summarize_message_content("user", str(msg.get("content") or ""), max_chars=120)
                        break
                if last_user and last_user not in base:
                    resolved = f"{last_user} {base}".strip()
                else:
                    resolved = base
        if _queries_need_history_anchors(clean_queries, clean_entities, user_input):
            repaired: list[str] = []
            prefix = " ".join((clean_entities or _extract_history_anchor_terms(history))[:4]).strip()
            seed_queries = clean_queries or [(user_input or "").strip()]
            for q in seed_queries:
                q = (q or "").strip()
                if not q:
                    continue
                if prefix and not any(tok and tok in q for tok in prefix.split()):
                    repaired.append(f"{prefix} {q}".strip())
                else:
                    repaired.append(q)
            if prefix:
                # Ensure at least one strongly anchored query.
                if not any(prefix.split()[0] in q for q in repaired if prefix.split()):
                    repaired.insert(0, f"{prefix} {(user_input or '').strip()}".strip())
            clean_queries = _normalize_text_list(repaired, max_items=3)

    if not clean_queries:
        clean_queries = [resolved or (user_input or "").strip() or "search"]
    if not resolved:
        resolved = clean_queries[0]

    return {
        "type": "search",
        "resolved_query": resolved,
        "queries": clean_queries,
        "entities": clean_entities,
        "is_followup": followup,
        "topic_changed": bool(topic_changed),
    }


def _fallback_search_analysis(
    user_input: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """History-aware fallback when model output is missing or invalid."""
    text = (user_input or "").strip() or "search"
    followup = _looks_like_followup_query(text, history)
    entities = _extract_history_anchor_terms(history) if history else []
    if followup and entities:
        resolved = f"{' '.join(entities[:4])} {text}".strip()
        queries = [resolved]
        # Only add a timezone-specific query when the follow-up is actually about time;
        # otherwise wasting a query slot on "时间 北京时间" admits irrelevant sources.
        if _FOLLOWUP_HINT_RE.search(text) or "时间" in text or "time" in text.lower():
            queries.append(f"{' '.join(entities[:3])} 北京时间".strip())
        # Drop near-duplicates
        queries = _normalize_text_list(queries, max_items=3)
        return _build_search_analysis_result(
            queries=queries,
            resolved_query=resolved,
            entities=entities,
            is_followup=True,
            topic_changed=False,
            user_input=text,
            history=history,
        )
    return _build_search_analysis_result(
        queries=[text],
        resolved_query=text,
        entities=entities,
        is_followup=followup,
        topic_changed=False,
        user_input=text,
        history=history,
    )


class LLMClient:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "deepseek-v4-pro"):
        self.client = create_openai_client(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,  # 禁用 SDK 自动重试，由上层统一处理（含 Connection error）
            timeout=_LLM_TIMEOUT,
            connect_timeout=_LLM_CONNECT_TIMEOUT,
        )
        self.model = model
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    async def _call_with_retry(self, messages: list, retries: int = 2, timeout: float = None) -> Any:
        """带重试的 LLM 调用。处理超时/连接错误/429/5xx。使用指数退避 + 抖动。"""
        request_timeout = timeout or _LLM_TIMEOUT
        for attempt in range(retries + 1):
            try:
                async with _LLM_CONCURRENCY:
                    response = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                        ),
                        timeout=request_timeout,
                    )
                self._track_usage(response)
                return response
            except asyncio.TimeoutError:
                logger.warning(
                    "[LLM] 请求超时 (%.0fs), 重试 %d/%d",
                    request_timeout, attempt + 1, retries,
                )
                if attempt >= retries:
                    raise
                await asyncio.sleep(_retry_backoff_seconds(attempt))
            except Exception as e:
                provider_message = _provider_error_message(e)
                if provider_message:
                    raise LLMProviderConfigurationError(provider_message) from e
                if _is_retryable_llm_error(e) and attempt < retries:
                    wait = _retry_backoff_seconds(attempt)
                    status_code = _status_code_of(e)
                    logger.warning(
                        "[LLM] 请求失败 (%s%s), %.1f 秒后重试 (%d/%d)...",
                        type(e).__name__,
                        f"/{status_code}" if status_code else "",
                        wait,
                        attempt + 1,
                        retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

    def _track_usage(self, response):
        """Track token usage from response."""
        if hasattr(response, 'usage') and response.usage:
            self.total_prompt_tokens += getattr(response.usage, 'prompt_tokens', 0) or 0
            self.total_completion_tokens += getattr(response.usage, 'completion_tokens', 0) or 0

    def _extract_response_content(self, response: Any) -> str:
        """Extract message content from SDK objects or gateway string responses."""
        if response is None:
            return ""
        if isinstance(response, str):
            sse_content = self._extract_sse_content(response)
            if sse_content:
                return sse_content
            return response
        if isinstance(response, bytes):
            return response.decode("utf-8", errors="replace")
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message", {})
                if isinstance(message, dict):
                    return message.get("content", "") or ""
                if isinstance(message, str):
                    return message
            return response.get("content", "") or response.get("output_text", "") or ""

        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if content is not None:
                return content
            if isinstance(message, str):
                return message
        return getattr(response, "content", None) or getattr(response, "output_text", "") or ""

    def _extract_sse_content(self, text: str) -> str:
        """Extract concatenated delta content from SSE-formatted response text."""
        if "data:" not in text:
            return ""

        chunks = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue

            payload = line.removeprefix("data:").strip()
            if not payload or payload == "[DONE]":
                continue

            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue

            for choice in data.get("choices", []):
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    chunks.append(content)

        return "".join(chunks)

    def _extract_json(self, text: str) -> Optional[Any]:
        """
        健壮地从 LLM 响应中提取 JSON。
        优先级: 直接解析 > markdown 代码块 > 从正文扫描 JSON 对象/数组
        """
        if not text:
            return None

        def parse_candidate(candidate: str) -> Optional[Any]:
            try:
                data = json.loads(candidate.strip())
                if isinstance(data, (dict, list)):
                    return data
            except (json.JSONDecodeError, ValueError):
                return None
            return None

        # 1. 直接尝试解析整段文本
        text = text.strip()
        data = parse_candidate(text)
        if data is not None:
            return data

        # 2. 尝试从 markdown 代码块提取（```json ... ``` 或 ``` ... ```）
        code_block_patterns = [
            r'```json\s*\n?(.*?)\n?\s*```',
            r'```\s*\n?(.*?)\n?\s*```',
        ]
        for pattern in code_block_patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                data = parse_candidate(match.group(1))
                if data is not None:
                    return data

        # 3. 从正文中扫描第一个可解析 JSON。JSONDecoder 会正确处理字符串内括号。
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "{[":
                continue
            try:
                data, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, (dict, list)):
                return data

        return None

    def _build_context_messages(self, history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
        """Build compact chat history for LLM calls (summarize long/HTML assistant turns)."""
        if not history:
            return []

        result = []
        # Keep last 12 messages to bound tokens while preserving multi-turn intent.
        recent = [msg for msg in history if isinstance(msg, dict)][-12:]
        for msg in recent:
            role = msg.get("role", "user")
            if role not in ("user", "assistant", "system"):
                role = "user"
            max_chars = 600 if role == "assistant" else 900
            content = _summarize_message_content(role, str(msg.get("content") or ""), max_chars=max_chars)
            if not content:
                # Preserve user/assistant alternation: a model fed two consecutive
                # user turns can mis-attribute the follow-up. Emit a minimal stub.
                if role == "assistant":
                    content = "…"
                else:
                    # user turns with empty content carry no information; drop only
                    # when the neighboring turn is still the opposite role.
                    if result and result[-1]["role"] == "user":
                        continue
                    content = "…"
            result.append({"role": role, "content": content})
        return result

    async def analyze_task(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        [02] AI Model: Task Analysis
        Returns search analysis with resolved_query/queries/entities, or a direct URL.
        """
        # Cache lookup — if same query was analyzed recently, reuse result
        import time as _time
        cache_key = f"task:{_cache_digest({'input': user_input.strip().lower(), 'history': _history_cache_digest(history)})}"
        now = _time.time()
        if cache_key in _ANALYSIS_CACHE:
            cached_result, cached_time = _ANALYSIS_CACHE[cache_key]
            if now - cached_time < _ANALYSIS_CACHE_TTL:
                logger.info("[Task Analysis] 缓存命中: %s", user_input[:50])
                return _clone_cached_analysis_result(cached_result)

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = TASK_ANALYSIS_PROMPT.format(current_time=current_time)

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history if available (summarized)
        context = self._build_context_messages(history)
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_input})

        try:
            logger.info("[Task Analysis] 输入: %s", _truncate_for_log(user_input, 80))
            response = await self._call_with_retry(messages, retries=0, timeout=_LLM_SHORT_TIMEOUT)
            content = self._extract_response_content(response)

            data = self._extract_json(content)
            if isinstance(data, dict):
                # Validate the structure
                if data.get("type") == "direct" and data.get("url"):
                    result = {"type": "direct", "url": str(data["url"]).strip()}
                    logger.info("[Task Analysis] 直接 URL: %s", result["url"][:100])
                    _cache_analysis_result(cache_key, result)
                    return result

                queries = None
                if data.get("type") == "search" and data.get("queries") is not None:
                    queries = _normalize_text_list(data["queries"], max_items=3)
                elif data.get("queries") is not None:
                    queries = _normalize_text_list(data["queries"], max_items=3)
                elif data.get("query") is not None:
                    queries = _normalize_text_list(data["query"], max_items=1)

                resolved_query_raw = str(
                    data.get("resolved_query") or data.get("standalone_query") or ""
                ).strip()
                # If the model gave a standalone resolved_query but no usable queries
                # (queries:null/empty), seed queries from the resolved intent instead
                # of discarding it and falling back to a raw echo of user_input.
                if not queries and resolved_query_raw:
                    queries = _normalize_text_list([resolved_query_raw], max_items=3)

                if queries:
                    result = _build_search_analysis_result(
                        queries=queries,
                        resolved_query=resolved_query_raw,
                        entities=_normalize_text_list(data.get("entities"), max_items=8),
                        is_followup=_coerce_bool(data.get("is_followup")),
                        topic_changed=_coerce_bool(data.get("topic_changed")),
                        user_input=user_input,
                        history=history,
                    )
                    logger.info(
                        "[Task Analysis] resolved=%s queries=%s entities=%s followup=%s",
                        _truncate_for_log(result.get("resolved_query", ""), 80),
                        json.dumps(result.get("queries", []), ensure_ascii=False)[:160],
                        result.get("entities", []),
                        result.get("is_followup"),
                    )
                    _cache_analysis_result(cache_key, result)
                    return result

            # Fallback
            logger.warning("[Task Analysis] JSON 解析失败或结构无效，使用 history-aware fallback")
            result = _fallback_search_analysis(user_input, history)
            # Do not cache invalid-parse fallback when history is empty and we only echo input;
            # transient failures already skip cache. Cache successful structured repairs only.
            if result.get("queries") and result.get("resolved_query"):
                _cache_analysis_result(cache_key, result)
            return result

        except LLMProviderConfigurationError:
            raise
        except Exception as e:
            logger.error("Error in analyze_task: %s", e)
            # Do not cache transient failures.
            return _fallback_search_analysis(user_input, history)

    async def assess_relevance(self, query: str, snippets: List[Dict]) -> List[int]:
        """
        [04] AI Model: Relevance Assessment
        Input: Query and a list of snippets with IDs.
        Returns: List of IDs (integers) that are relevant and worth deep crawling.
        """
        # Cache lookup — if same query+snippets was analyzed recently, reuse result
        import time as _time
        cache_key = f"rel:{_cache_digest({'query': query.strip().lower(), 'snippets': _snippet_cache_digest(snippets)})}"
        now = _time.time()
        if cache_key in _ANALYSIS_CACHE:
            cached_result, cached_time = _ANALYSIS_CACHE[cache_key]
            if now - cached_time < _ANALYSIS_CACHE_TTL:
                logger.info("[Relevance] 缓存命中: %s", query[:50])
                return _clone_cached_analysis_result(cached_result)

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = RELEVANCE_ASSESSMENT_PROMPT.format(current_time=current_time)

        user_message = f"Query: {query}\n\nSnippets:\n"
        for item in snippets:
            date_info = f"Date: {item.get('date', 'N/A')}\n" if item.get('date') else ""
            user_message += f"ID [{item['id']}]: Title: {item['title']}\n{date_info}Snippet: {item['snippet']}\n\n"

        try:
            logger.info("[Relevance Assessment] 评估 %d 个搜索结果", len(snippets))
            response = await self._call_with_retry([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ], retries=0, timeout=_LLM_SHORT_TIMEOUT)
            content = self._extract_response_content(response)

            data = self._extract_json(content)
            if isinstance(data, dict):
                ids = _normalize_int_list(data.get("relevant_ids", []))
                logger.info("[Relevance Assessment] 选定 ID: %s", ids)
                _cache_analysis_result(cache_key, ids)
                return ids

            logger.warning("[Relevance Assessment] JSON 解析失败，返回前 3 个")
            return [s['id'] for s in snippets[:3]]
        except LLMProviderConfigurationError:
            raise
        except Exception as e:
            logger.error("Error in assess_relevance: %s", e)
            # Fallback: return top 3 if parsing fails
            return [s['id'] for s in snippets[:3]]

    async def decide_click_elements(self, query: str, elements: List[Dict]) -> List[str]:
        """
        [New] AI Model: Decide which elements to click
        Input: Query and a list of interactive elements (id, text, type).
        Returns: List of element id strings to click (e.g. "js-interact-0").
        """
        if not elements:
            return []

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = CLICK_DECISION_PROMPT.format(current_time=current_time)

        user_message = f"Query: {query}\n\nClickable Elements:\n"
        for el in elements:
            user_message += f"ID [{el['id']}]: [{el['tag']}] {el['text']}\n"

        try:
            logger.info("[Click Decision] 评估 %d 个可点击元素", len(elements))
            response = await self._call_with_retry([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ], retries=0, timeout=_LLM_SHORT_TIMEOUT)
            content = self._extract_response_content(response)

            data = self._extract_json(content)
            if isinstance(data, dict):
                clicked = _normalize_text_list(data.get("clicked_ids", []), max_items=3)
                valid_ids = {str(el.get("id", "")) for el in elements if el.get("id") is not None}
                clicked = [cid for cid in clicked if cid in valid_ids]
                # Limit to max 3 clicks per page to avoid excessive interaction
                if len(clicked) > 3:
                    logger.info("[Click Decision] 截断点击列表: %s → 前 3 个", clicked)
                    clicked = clicked[:3]
                logger.info("[Click Decision] 决定点击: %s", clicked)
                return clicked
            logger.info("[Click Decision] 不点击任何元素")
            return []
        except LLMProviderConfigurationError:
            raise
        except Exception as e:
            logger.error("Error in decide_click_elements: %s", e)
            return []

    async def verify_citation_claims(
        self,
        items: List[Dict[str, Any]],
        *,
        timeout: float = 6.0,
    ) -> Dict[str, Dict[str, Any]]:
        """Batch-verify bounded claim/quote pairs. Fail-closed parsing; callers fail open.

        Returns a mapping keyed by the caller-provided item ``id``. Unknown,
        duplicate, missing, malformed, or out-of-contract results are ignored.
        """
        bounded: list[dict[str, str]] = []
        valid_ids: set[str] = set()
        for item in (items or [])[:3]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            claim = str(item.get("claim") or "").strip()[:320]
            quote = str(item.get("quote") or "").strip()[:480]
            title = str(item.get("title") or "").strip()[:120]
            if not item_id or not claim or not quote or item_id in valid_ids:
                continue
            valid_ids.add(item_id)
            bounded.append({"id": item_id, "claim": claim, "quote": quote, "source_title": title})
        if not bounded:
            return {}

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        messages = [
            {"role": "system", "content": CITATION_VERIFICATION_PROMPT.format(current_time=current_time)},
            {"role": "user", "content": json.dumps({"items": bounded}, ensure_ascii=False)},
        ]
        response = await self._call_with_retry(messages, retries=0, timeout=max(1.0, min(10.0, timeout)))
        parsed = self._extract_json(self._extract_response_content(response))
        results = parsed.get("results") if isinstance(parsed, dict) else None
        if not isinstance(results, list):
            return {}

        out: dict[str, dict[str, Any]] = {}
        allowed = {"SUPPORTED", "CONTRADICTED", "NOT_ENOUGH_INFO"}
        for raw in results:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("id") or "").strip()
            verdict = str(raw.get("verdict") or "").strip().upper()
            if item_id not in valid_ids or item_id in out or verdict not in allowed:
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
            except (TypeError, ValueError):
                confidence = 0.0
            out[item_id] = {
                "verdict": verdict,
                "confidence": round(confidence, 3),
                "reason": str(raw.get("reason") or "").strip()[:200],
            }
        return out

    async def generate_answer(self, query: str, sources: List[Dict], history: Optional[List[Dict[str, str]]] = None, stream_callback: Optional[Callable[[str], None]] = None, live_artifacts_mode: bool = False, canvas_mode: bool = False) -> Dict[str, Any]:
        """
        [09] AI Model: Generation & Evaluation
        Input: Query and full content of selected sources.
        Returns: {"status": "sufficient"|"insufficient", "answer": "..."}
        """
        # NOTE: generate_answer does NOT use cache — the same query with different sources
        # should produce different results. Caching by query alone caused a collision bug
        # where analyze_task's cached result was returned instead.

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        live_artifacts_requested = bool(live_artifacts_mode or canvas_mode)
        prompt_template = (
            ANSWER_GENERATION_LIVE_ARTIFACTS_PROMPT
            if live_artifacts_requested
            else ANSWER_GENERATION_PROMPT
        )
        system_prompt = prompt_template.format(current_time=current_time)
        if live_artifacts_requested:
            system_prompt = f"{system_prompt}\n\n{select_live_artifacts_protocol(query)}"

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history context if available
        context = self._build_context_messages(history)
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append({"role": role, "content": content})

        user_message = f"Question: {query}\n\nSources:\n"
        for src in sources:
            date_info = f" (Date: {src.get('date')})" if src.get('date') else ""
            user_message += f"Source [{src['id']}] (Title: {src['title']}{date_info}):\n{src['content']}\n\n"

        messages.append({"role": "user", "content": user_message})

        try:
            logger.info(
                "[Generate Answer] 使用 %d 个来源生成答案 (stream=%s)",
                len(sources),
                "yes" if stream_callback else "no",
            )

            max_attempts = max(1, _GENERATE_ANSWER_RETRIES)
            last_error: BaseException | None = None

            for attempt in range(max_attempts):
                response = None
                stream_slot_acquired = False
                full_content = ""
                status = "sufficient"
                parsing_header = True
                header_buffer = ""
                answer_started = False
                live_stream_buffer = ""
                live_streaming_enabled = False
                streamed_any = False

                def maybe_stream_answer(content: str):
                    nonlocal live_stream_buffer, live_streaming_enabled, streamed_any
                    if status != "sufficient" or not stream_callback or not content:
                        return
                    if not live_artifacts_requested:
                        stream_callback(content)
                        streamed_any = True
                        return
                    if live_streaming_enabled:
                        stream_callback(content)
                        streamed_any = True
                        return

                    live_stream_buffer += content
                    if _is_streamable_live_artifact_answer(live_stream_buffer):
                        live_streaming_enabled = True
                        stream_callback(live_stream_buffer)
                        live_stream_buffer = ""
                        streamed_any = True

                try:
                    await _LLM_CONCURRENCY.acquire()
                    stream_slot_acquired = True
                    try:
                        response = await asyncio.wait_for(
                            self.client.chat.completions.create(
                                model=self.model,
                                messages=messages,
                                stream=True,
                            ),
                            timeout=_LLM_TIMEOUT,
                        )

                        async for chunk in response:
                            if chunk.choices and chunk.choices[0].delta.content:
                                content = chunk.choices[0].delta.content
                                full_content += content

                                if parsing_header:
                                    header_buffer += content
                                    status_match = _ANSWER_FIELD_STATUS_RE.search(header_buffer)
                                    if status_match and "\n" in header_buffer[status_match.end():]:
                                        status_value = (status_match.group(1) or "").strip().lower()
                                        if (
                                            "insufficient" in status_value
                                            or status_value in {"不足", "不充分", "不够", "不完整"}
                                            or status_value.startswith("不足")
                                        ):
                                            status = "insufficient"

                                    answer_match = _ANSWER_FIELD_ANSWER_RE.search(header_buffer)
                                    if answer_match:
                                        answer_chunk = header_buffer[answer_match.end():]
                                        parsing_header = False
                                        answer_started = True
                                        maybe_stream_answer(answer_chunk)

                                    if len(header_buffer) > 500 and not answer_started:
                                        parsing_header = False
                                        maybe_stream_answer(header_buffer)
                                else:
                                    maybe_stream_answer(content)

                            elif chunk.choices and chunk.choices[0].finish_reason:
                                break
                    finally:
                        if stream_slot_acquired:
                            _LLM_CONCURRENCY.release()
                            stream_slot_acquired = False

                    envelope = _parse_answer_envelope(full_content)
                    status = envelope["status"] if envelope["had_envelope"] else status
                    missing_info = envelope["missing_info"]
                    final_answer = envelope["answer"]

                    if live_artifacts_requested:
                        # Always normalize: strip envelopes and keep raw HTML intact.
                        # Insufficient partials are re-wrapped by the workflow with a banner.
                        final_answer = ensure_live_artifact_answer(final_answer)

                    return {
                        "status": status,
                        "answer": final_answer.strip(),
                        "missing_info": missing_info,
                    }

                except asyncio.TimeoutError as e:
                    last_error = e
                    logger.warning(
                        "[Generate Answer] 请求超时, 重试 %d/%d",
                        attempt + 1,
                        max_attempts - 1,
                    )
                    if attempt >= max_attempts - 1:
                        return {"status": "sufficient", "answer": "生成答案超时，请重试。"}
                    await asyncio.sleep(_retry_backoff_seconds(attempt, base=2.0))
                    continue
                except Exception as e:
                    last_error = e
                    provider_message = _provider_error_message(e)
                    if provider_message:
                        raise LLMProviderConfigurationError(provider_message) from e

                    # Only restart the whole stream if nothing has been sent to the UI yet.
                    can_retry = (
                        _is_retryable_llm_error(e)
                        and attempt < max_attempts - 1
                        and not streamed_any
                        and not full_content
                    )
                    if can_retry:
                        wait = _retry_backoff_seconds(attempt, base=2.0)
                        status_code = _status_code_of(e)
                        logger.warning(
                            "[Generate Answer] %s%s, %.1f 秒后重试 (%d/%d)...",
                            type(e).__name__,
                            f"/{status_code}" if status_code else "",
                            wait,
                            attempt + 1,
                            max_attempts - 1,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise

            if last_error is not None:
                raise last_error
            return {"status": "sufficient", "answer": "生成答案时出错: 未收到模型响应。"}

        except LLMProviderConfigurationError:
            raise
        except Exception as e:
            logger.error("Error in generate_answer: %s", e)
            return {"status": "sufficient", "answer": f"生成答案时出错: {e}"}


def _truncate_for_log(text: str, max_len: int = 50) -> str:
    """截断文本用于日志输出，避免泄露完整查询。"""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + "..."
