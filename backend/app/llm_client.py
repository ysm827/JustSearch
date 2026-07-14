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
)

logger = logging.getLogger(__name__)

# LLM 调用超时
_LLM_TIMEOUT = 120  # 秒（默认，用于 generate_answer）
_LLM_SHORT_TIMEOUT = 30  # 秒（用于 analyze_task / assess_relevance 等短操作）

# 并发 LLM 请求限制
_LLM_CONCURRENCY = asyncio.Semaphore(5)

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


def ensure_live_artifact_answer(answer: str) -> str:
    """Return an inline Live Artifact even when a model falls back to Markdown."""
    stripped = _strip_live_artifact_fence(answer)
    if _looks_like_inline_live_artifact(stripped):
        return stripped
    return _markdown_to_live_artifact_html(stripped)


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
            "content": str(msg.get("content", "")),
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


class LLMClient:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "deepseek-v4-pro"):
        self.client = create_openai_client(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,  # 禁用自动重试，由上层统一处理
            timeout=_LLM_TIMEOUT,
        )
        self.model = model
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    async def _call_with_retry(self, messages: list, retries: int = 2, timeout: float = None) -> Any:
        """带重试的 LLM 调用。处理 429/500 等可重试错误。使用指数退避 + 抖动。"""
        import random as _rand
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
                logger.warning("[LLM] 请求超时 (%.0fs), 重试 %d/%d", request_timeout, attempt + 1, retries)
                if attempt >= retries:
                    raise
                wait = (2 ** attempt) * 1.5 + _rand.uniform(0, 1)
                await asyncio.sleep(wait)
            except Exception as e:
                status_code = getattr(e, 'status_code', 0)
                provider_message = _provider_error_message(e)
                if provider_message:
                    raise LLMProviderConfigurationError(provider_message) from e
                # 可重试的状态码
                if status_code in (429, 500, 502, 503) and attempt < retries:
                    wait = (2 ** attempt) * 1.5 + _rand.uniform(0, 1)
                    logger.warning("[LLM] 请求失败 (%d), %.1f 秒后重试 (%d/%d)...", status_code, wait, attempt + 1, retries)
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
        """从历史记录中构建上下文消息，保留完整上下文内容。"""
        if not history:
            return []

        result = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            result.append({"role": role, "content": content})
        return result

    async def analyze_task(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        [02] AI Model: Task Analysis
        Returns: {"type": "search", "queries": ["query1", "query2"]} or {"type": "direct", "url": "..."}
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

        # Add conversation history if available
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
                elif data.get("type") == "search" and data.get("queries"):
                    queries = _normalize_text_list(data["queries"], max_items=3)
                    if queries:
                        result = {"type": "search", "queries": queries}
                        logger.info("[Task Analysis] 查询: %s", json.dumps(result, ensure_ascii=False)[:200])
                        _cache_analysis_result(cache_key, result)
                        return result
                # If data has queries but no type, fix it
                elif data.get("queries"):
                    queries = _normalize_text_list(data["queries"], max_items=3)
                    if queries:
                        result = {"type": "search", "queries": queries}
                        _cache_analysis_result(cache_key, result)
                        return result
                elif data.get("query"):
                    queries = _normalize_text_list(data["query"], max_items=1)
                    if queries:
                        result = {"type": "search", "queries": queries}
                        _cache_analysis_result(cache_key, result)
                        return result

            # Fallback
            logger.warning("[Task Analysis] JSON 解析失败或结构无效，使用 fallback")
            result = {"type": "search", "queries": [user_input]}
            _cache_analysis_result(cache_key, result)
            return result

        except LLMProviderConfigurationError:
            raise
        except Exception as e:
            logger.error("Error in analyze_task: %s", e)
            result = {"type": "search", "queries": [user_input]}
            return result

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
            logger.info("[Generate Answer] 使用 %d 个来源生成答案 (stream=%s)", len(sources), "yes" if stream_callback else "no")
            
            # Use retry wrapper for API call — handle 429 rate limits
            max_retries = 3
            response = None
            stream_slot_acquired = False
            for retry in range(max_retries):
                try:
                    await _LLM_CONCURRENCY.acquire()
                    stream_slot_acquired = True
                    try:
                        response = await asyncio.wait_for(
                            self.client.chat.completions.create(
                                model=self.model,
                                messages=messages,
                                stream=True
                            ),
                            timeout=_LLM_TIMEOUT,
                        )
                    except BaseException:
                        if stream_slot_acquired:
                            _LLM_CONCURRENCY.release()
                            stream_slot_acquired = False
                        raise
                    break
                except asyncio.TimeoutError:
                    logger.warning("[Generate Answer] 请求超时, 重试 %d/%d", retry + 1, max_retries)
                    if retry >= max_retries - 1:
                        return {"status": "sufficient", "answer": "生成答案超时，请重试。"}
                    await asyncio.sleep(2.0 * (retry + 1))
                except Exception as e:
                    provider_message = _provider_error_message(e)
                    if provider_message:
                        raise LLMProviderConfigurationError(provider_message) from e
                    if "429" in str(e) and retry < max_retries - 1:
                        wait_time = 2.0 * (retry + 1)
                        logger.warning("[Generate Answer] 429 错误, %.1f 秒后重试 (%d/%d)...", wait_time, retry + 1, max_retries)
                        await asyncio.sleep(wait_time)
                    else:
                        raise

            if response is None:
                if stream_slot_acquired:
                    _LLM_CONCURRENCY.release()
                    stream_slot_acquired = False
                return {"status": "sufficient", "answer": "生成答案时出错: 未收到模型响应。"}

            full_content = ""
            status = "sufficient"
            parsing_header = True
            header_buffer = ""
            answer_started = False
            live_stream_buffer = ""
            live_streaming_enabled = False

            def maybe_stream_answer(content: str):
                nonlocal live_stream_buffer, live_streaming_enabled
                if status != "sufficient" or not stream_callback or not content:
                    return
                if not live_artifacts_requested:
                    stream_callback(content)
                    return
                if live_streaming_enabled:
                    stream_callback(content)
                    return

                live_stream_buffer += content
                if _is_streamable_live_artifact_answer(live_stream_buffer):
                    live_streaming_enabled = True
                    stream_callback(live_stream_buffer)
                    live_stream_buffer = ""

            try:
                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_content += content

                        if parsing_header:
                            header_buffer += content
                            # Check for Status
                            if "Status:" in header_buffer and "\n" in header_buffer.split("Status:")[1]:
                                status_line = [line for line in header_buffer.split("\n") if "Status:" in line][0]
                                if "insufficient" in status_line.lower():
                                    status = "insufficient"

                            # Check for Answer start
                            if "Answer:" in header_buffer:
                                parts = header_buffer.split("Answer:", 1)
                                # If we have content after Answer:, that's the start of the answer
                                if len(parts) > 1:
                                    answer_chunk = parts[1]
                                    parsing_header = False
                                    answer_started = True
                                    maybe_stream_answer(answer_chunk)

                            # Safety valve: if buffer gets too long without Answer:, maybe model didn't follow format
                            if len(header_buffer) > 500 and not answer_started:
                                parsing_header = False
                                # Assume whole thing is answer if status check passed or failed
                                maybe_stream_answer(header_buffer)

                        else:
                            # Streaming answer
                            maybe_stream_answer(content)

                    # Also handle empty delta (role/tool_calls) to avoid blocking
                    elif chunk.choices and chunk.choices[0].finish_reason:
                        break
            finally:
                if stream_slot_acquired:
                    _LLM_CONCURRENCY.release()
                    stream_slot_acquired = False

            final_answer = full_content
            missing_info = ""

            if "Missing_Info:" in final_answer:
                missing_info = final_answer.split("Missing_Info:", 1)[1]
                if "Answer:" in missing_info:
                    missing_info = missing_info.split("Answer:", 1)[0]
                missing_info = missing_info.strip()

            if "Answer:" in final_answer:
                final_answer = final_answer.split("Answer:", 1)[1].strip()
            elif "Status:" in final_answer:
                lines = final_answer.split("\n")
                final_answer = "\n".join(
                    line for line in lines
                    if not line.startswith("Status:") and not line.startswith("Missing_Info:")
                )

            if live_artifacts_requested and status == "sufficient":
                final_answer = ensure_live_artifact_answer(final_answer)

            return {
                "status": status,
                "answer": final_answer.strip(),
                "missing_info": missing_info,
            }

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
