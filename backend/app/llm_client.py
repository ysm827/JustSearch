import json
import logging
import os
import re
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any
from openai import AsyncOpenAI
from .prompts import TASK_ANALYSIS_PROMPT, RELEVANCE_ASSESSMENT_PROMPT, CLICK_DECISION_PROMPT, ANSWER_GENERATION_PROMPT

logger = logging.getLogger(__name__)

# 默认上下文轮数，可通过 settings 覆盖
_DEFAULT_CONTEXT_TURNS = 6

# LLM 调用超时
_LLM_TIMEOUT = 120  # 秒（默认，用于 generate_answer）
_LLM_SHORT_TIMEOUT = 60  # 秒（用于 analyze_task / assess_relevance 等短操作）

# 并发 LLM 请求限制
_LLM_CONCURRENCY = asyncio.Semaphore(5)


class LLMClient:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "deepseek-ai/deepseek-v3.2", max_context_turns: int = _DEFAULT_CONTEXT_TURNS):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,  # 禁用自动重试，由上层统一处理
            timeout=_LLM_TIMEOUT,
        )
        self.model = model
        self.max_context_turns = max_context_turns
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    async def _call_with_retry(self, messages: list, retries: int = 2, timeout: float = None) -> Any:
        """带重试的 LLM 调用。处理 429/500 等可重试错误。使用指数退避 + 抖动。"""
        import httpx
        import random as _rand
        request_timeout = timeout or _LLM_TIMEOUT
        for attempt in range(retries + 1):
            async with _LLM_CONCURRENCY:
                try:
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
                    err_str = str(e)
                    status_code = getattr(e, 'status_code', 0)
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

    def _extract_json(self, text: str) -> Optional[Dict]:
        """
        健壮地从 LLM 响应中提取 JSON。
        优先级: 直接解析 > markdown 代码块 > 贪婪正则
        """
        if not text:
            return None

        # 1. 直接尝试解析整段文本
        text = text.strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. 尝试从 markdown 代码块提取（```json ... ``` 或 ``` ... ```）
        code_block_patterns = [
            r'```json\s*\n?(.*?)\n?\s*```',
            r'```\s*\n?(.*?)\n?\s*```',
        ]
        for pattern in code_block_patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1).strip())
                    if isinstance(data, dict):
                        return data
                except (json.JSONDecodeError, ValueError):
                    continue

        # 3. 找到最外层的 { ... } 或 [ ... ] 并尝试解析
        # 使用括号匹配来处理嵌套结构
        for opener, closer in [('{', '}'), ('[', ']')]:
            start = text.find(opener)
            if start == -1:
                continue
            depth = 0
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, (dict, list)):
                                return data
                        except (json.JSONDecodeError, ValueError):
                            break

        return None

    def _build_context_messages(self, history: Optional[List[Dict[str, str]]], max_turns: int) -> List[Dict[str, str]]:
        """从历史记录中构建上下文消息，限制轮数。对 assistant 消息做摘要截断以节省 token。"""
        if not history:
            return []
        
        recent = history[-max_turns:]
        result = []
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "assistant" and len(content) > 500:
                truncated = content[:400]
                # Try to find a sentence boundary for cleaner truncation
                last_stop = max(
                    truncated.rfind('。'),
                    truncated.rfind('.'),
                    truncated.rfind('？'),
                    truncated.rfind('?'),
                    truncated.rfind('！'),
                    truncated.rfind('!'),
                )
                if last_stop > len(truncated) * 0.5:
                    content = content[:last_stop + 1] + "...(答案已截断)"
                else:
                    content = truncated + "...(答案已截断)"
            result.append({"role": role, "content": content})
        return result

    async def analyze_task(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        [02] AI Model: Task Analysis
        Returns: {"type": "search", "queries": ["query1", "query2"]} or {"type": "direct", "url": "..."}
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = TASK_ANALYSIS_PROMPT.format(current_time=current_time)

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history if available
        context = self._build_context_messages(history, self.max_context_turns)
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_input})

        try:
            logger.info("[Task Analysis] 输入: %s", _truncate_for_log(user_input, 80))
            response = await self._call_with_retry(messages, timeout=_LLM_SHORT_TIMEOUT)
            content = response.choices[0].message.content

            data = self._extract_json(content)
            if data:
                logger.info("[Task Analysis] 结果: %s", json.dumps(data, ensure_ascii=False)[:200])
                return data

            # Fallback
            logger.warning("[Task Analysis] JSON 解析失败，使用 fallback")
            return {"type": "search", "queries": [user_input]}

        except Exception as e:
            logger.error("Error in analyze_task: %s", e)
            return {"type": "search", "queries": [user_input]}

    async def assess_relevance(self, query: str, snippets: List[Dict]) -> List[int]:
        """
        [04] AI Model: Relevance Assessment
        Input: Query and a list of snippets with IDs.
        Returns: List of IDs (integers) that are relevant and worth deep crawling.
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = RELEVANCE_ASSESSMENT_PROMPT.format(current_time=current_time)

        user_message = f"Query: {_truncate_for_log(query)}\n\nSnippets:\n"
        for item in snippets:
            date_info = f"Date: {item.get('date', 'N/A')}\n" if item.get('date') else ""
            user_message += f"ID [{item['id']}]: Title: {item['title']}\n{date_info}Snippet: {item['snippet']}\n\n"

        try:
            logger.info("[Relevance Assessment] 评估 %d 个搜索结果", len(snippets))
            response = await self._call_with_retry([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ], timeout=_LLM_SHORT_TIMEOUT)
            content = response.choices[0].message.content

            data = self._extract_json(content)
            if data:
                ids = data.get("relevant_ids", [])
                logger.info("[Relevance Assessment] 选定 ID: %s", ids)
                return ids

            logger.warning("[Relevance Assessment] JSON 解析失败，返回前 3 个")
            return [s['id'] for s in snippets[:3]]
        except Exception as e:
            logger.error("Error in assess_relevance: %s", e)
            # Fallback: return top 3 if parsing fails
            return [s['id'] for s in snippets[:3]]

    async def decide_click_elements(self, query: str, elements: List[Dict]) -> List[int]:
        """
        [New] AI Model: Decide which elements to click
        Input: Query and a list of interactive elements (id, text, type).
        Returns: List of IDs to click.
        """
        if not elements:
            return []

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = CLICK_DECISION_PROMPT.format(current_time=current_time)

        user_message = f"Query: {_truncate_for_log(query)}\n\nClickable Elements:\n"
        # Limit elements to avoid token overflow
        for el in elements[:50]:
            user_message += f"ID [{el['id']}]: [{el['tag']}] {el['text'][:100]}\n"

        try:
            logger.info("[Click Decision] 评估 %d 个可点击元素", len(elements))
            response = await self._call_with_retry([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ], timeout=_LLM_SHORT_TIMEOUT)
            content = response.choices[0].message.content

            data = self._extract_json(content)
            if data:
                clicked = data.get("clicked_ids", [])
                logger.info("[Click Decision] 决定点击: %s", clicked)
                return clicked
            logger.info("[Click Decision] 不点击任何元素")
            return []
        except Exception as e:
            logger.error("Error in decide_click_elements: %s", e)
            return []

    async def generate_answer(self, query: str, sources: List[Dict], history: Optional[List[Dict[str, str]]] = None, stream_callback: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """
        [09] AI Model: Generation & Evaluation
        Input: Query and full content of selected sources.
        Returns: {"status": "sufficient"|"insufficient", "answer": "..."}
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        system_prompt = ANSWER_GENERATION_PROMPT.format(current_time=current_time)

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history context if available
        context = self._build_context_messages(history, self.max_context_turns)
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append({"role": role, "content": content})

        user_message = f"Question: {query}\n\nSources:\n"
        for src in sources:
            # 智能段落截取：在段落边界截断，而非硬切
            content_preview = _smart_truncate(src['content'], max_chars=8000)
            date_info = f" (Date: {src.get('date')})" if src.get('date') else ""
            user_message += f"Source [{src['id']}] (Title: {src['title']}{date_info}):\n{content_preview}\n\n"

        messages.append({"role": "user", "content": user_message})

        try:
            logger.info("[Generate Answer] 使用 %d 个来源生成答案 (stream=%s)", len(sources), "yes" if stream_callback else "no")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True
            )

            full_content = ""
            status = "sufficient"
            parsing_header = True
            header_buffer = ""
            answer_started = False

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
                            pre_answer = parts[0]
                            # If we have content after Answer:, that's the start of the answer
                            if len(parts) > 1:
                                answer_chunk = parts[1]
                                parsing_header = False
                                answer_started = True
                                if status == "sufficient" and stream_callback and answer_chunk:
                                    stream_callback(answer_chunk)

                        # Safety valve: if buffer gets too long without Answer:, maybe model didn't follow format
                        if len(header_buffer) > 500 and not answer_started:
                            parsing_header = False
                            # Assume whole thing is answer if status check passed or failed
                            if stream_callback:
                                stream_callback(header_buffer)

                    else:
                        # Streaming answer
                        if status == "sufficient" and stream_callback:
                            stream_callback(content)
                
                # Also handle empty delta (role/tool_calls) to avoid blocking
                elif chunk.choices and chunk.choices[0].finish_reason:
                    break

            # Post-processing to extract clean answer from full_content
            final_answer = full_content
            if "Answer:" in final_answer:
                final_answer = final_answer.split("Answer:", 1)[1].strip()
            elif "Status:" in final_answer:
                 # Fallback if Answer: tag missing but Status present
                 lines = final_answer.split("\n")
                 # Filter out metadata lines
                 final_answer = "\n".join([l for l in lines if not l.startswith("Status:") and not l.startswith("Missing_Info:")])

            return {"status": status, "answer": final_answer.strip()}

        except Exception as e:
            logger.error("Error in generate_answer: %s", e)
            return {"status": "sufficient", "answer": f"生成答案时出错: {e}"}


def _truncate_for_log(text: str, max_len: int = 50) -> str:
    """截断文本用于日志输出，避免泄露完整查询。"""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _smart_truncate(text: str, max_chars: int = 8000) -> str:
    """
    智能截取文本，尽量在段落/句子边界截断。
    如果文本本身短于限制，直接返回。
    """
    if not text or len(text) <= max_chars:
        return text or ""

    # 找到 max_chars 附近的段落边界（双换行）
    truncated = text[:max_chars]
    last_paragraph = truncated.rfind('\n\n')
    if last_paragraph > max_chars * 0.5:  # 至少保留 50% 的内容
        return truncated[:last_paragraph].rstrip() + "\n\n[... 内容已截取]"

    # 退而求其次：单换行边界
    last_newline = truncated.rfind('\n')
    if last_newline > max_chars * 0.5:
        return truncated[:last_newline].rstrip() + "\n[... 内容已截取]"

    # 最后手段：句号/问号/叹号（含中文标点）
    last_sentence = max(
        truncated.rfind('。'), truncated.rfind('.'),
        truncated.rfind('！'), truncated.rfind('!'),
        truncated.rfind('？'), truncated.rfind('?'),
        truncated.rfind('；'), truncated.rfind(';'),
    )
    if last_sentence > max_chars * 0.5:
        return truncated[:last_sentence + 1] + "[... 内容已截取]"

    return truncated + "[... 内容已截取]"
