import asyncio
import base64
import json
import logging
from typing import List, Dict, Callable, Any, Optional
from .llm_client import LLMClient
from .browser_manager import BrowserManager

logger = logging.getLogger(__name__)

class SearchWorkflow:
    def __init__(self, api_key: str, base_url: str, model: str, search_engine: str = "duckduckgo", max_results: int = 8, max_iterations: int = 5, interactive_search: bool = True, session_id: str = None, max_context_turns: int = 6, max_concurrent_pages: int = 3):
        self.llm = LLMClient(api_key, base_url, model, max_context_turns=max_context_turns)
        # Pass the search engine preference to the browser manager
        self.browser = BrowserManager(engine=search_engine, max_results=max_results)
        self.max_iterations = max_iterations
        self.history = []
        self.interactive_search = interactive_search
        self.session_id = session_id
        self.max_concurrent_pages = max_concurrent_pages
        # Content cache: url -> content (avoid re-crawling same URL across iterations)
        self._content_cache: Dict[str, str] = {}
        # Minimum content length to be considered useful (chars)
        self._min_content_length = 150

    def _normalize_url(self, url: str) -> str:
        """规范化 URL 用于去重。处理 Bing redirect URL 等特殊格式。"""
        if not url:
            return ''
        # 提取 Bing redirect URL 中的真实目标
        if 'bing.com/ck/a' in url and 'u=a1' in url:
            try:
                idx = url.index('u=a1')
                encoded = url[idx + 4:]  # 跳过 'u=a1'
                # URL-safe base64 解码
                padded = encoded + '=' * (4 - len(encoded) % 4)
                decoded = base64.urlsafe_b64decode(padded).decode('utf-8', errors='ignore')
                return decoded.lower().rstrip('/')
            except Exception:
                pass
        # Strip common tracking parameters
        try:
            from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
            parsed = urlparse(url)
            if parsed.query:
                params = parse_qs(parsed.query, keep_blank_values=True)
                # Remove known tracking params
                # Remove known tracking params + normalize mobile URLs
                tracking_keys = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term',
                                 'utm_content', 'fbclid', 'gclid', 'msclkid', 'ref', 'source',
                                 'share_token', 'is_shared', 's_bm', 'spm', 'from', 'bd_vid',
                                 'wvr', 'mod', 'lang', 'sudaref', 'isa', '_ent', 'vtype',
                                 'is_from_webapp', 'timestamp', 'share_wrap'}
                cleaned = {k: v for k, v in params.items() if k.lower() not in tracking_keys}
                new_query = urlencode(cleaned, doseq=True)
                # Normalize mobile URLs (m.example.com -> example.com for dedup)
                hostname = parsed.hostname or ''
                if hostname.startswith('m.'):
                    hostname = hostname[2:]
                url = urlunparse(parsed._replace(query=new_query, netloc=hostname if hostname != parsed.hostname else parsed.netloc))
        except Exception:
            pass
        # 通用规范化：去尾斜杠、小写
        return url.lower().rstrip('/')

    def _resolve_url(self, url: str) -> str:
        """将 Bing redirect URL 解析为真实目标 URL（用于显示）。"""
        if not url:
            return url
        if 'bing.com/ck/a' in url and 'u=a1' in url:
            try:
                idx = url.index('u=a1')
                encoded = url[idx + 4:]
                padded = encoded + '=' * (4 - len(encoded) % 4)
                decoded = base64.urlsafe_b64decode(padded).decode('utf-8', errors='ignore')
                return decoded
            except Exception:
                pass
        return url

    def _format_references(self, answer: str, sources: List[Dict]) -> str:
        """
        Helper to append a formatted reference list to the answer.
        """
        if not sources:
            return answer
        
        # Deduplicate by id — dict comprehension keeps last occurrence
        unique_sources = {src['id']: src for src in sources}
        sorted_sources = sorted(unique_sources.values(), key=lambda x: x['id'])
        
        ref_section = "\n\n---\n### 参考资料\n"
        for src in sorted_sources:
            title = src.get('title', 'Unknown Source').replace('\n', ' ').strip()
            url = self._resolve_url(src.get('url', '#'))
            date = src.get('date', '')
            date_str = f" ({date})" if date else ""
            ref_section += f"[{src['id']}] [{title}]({url}){date_str}  \n" 
            
        return answer + ref_section

    async def _handle_direct_url(self, url: str, visited_urls: set,
                                  progress_callback: Callable[[str], None],
                                  user_input: str, source_id_counter: int) -> tuple:
        """处理直接 URL 访问，返回 (new_sources, source_id_counter)。"""
        progress_callback(f"目标 URL: {url}")
        
        new_sources = []
        if url not in visited_urls:
            content = await self.browser.crawl_page(url, log_func=progress_callback, interactive_mode=self.interactive_search, query=user_input, llm_client=self.llm, session_id=self.session_id)
            visited_urls.add(url)
            source_id_counter += 1
            # 过滤超时/空内容
            if content == "[CRAWL_TIMEOUT]" or not content:
                progress_callback(f"跳过超时/空页面: {url}")
                return [], source_id_counter
            new_sources.append({
                "id": source_id_counter, 
                "url": url, 
                "title": "Direct URL", 
                "content": content
            })
        else:
            progress_callback(f"URL 已访问过，跳过: {url}")
        
        return new_sources, source_id_counter

    async def _handle_search(self, search_queries: list, search_history: list,
                              visited_urls: set, iteration: int,
                              progress_callback: Callable[[str], None],
                              user_input: str, source_id_counter: int) -> tuple:
        """处理搜索引擎查询，返回 (new_sources, source_id_counter, search_results_count)。"""
        # Deduplicate and check against history
        valid_queries = []
        for q in search_queries:
            if q not in search_history:
                valid_queries.append(q)
                search_history.append(q)
        
        # If all were duplicates (e.g. Iter 2 suggests same query), allow at least one if it's new to this batch?
        if not valid_queries and iteration == 1 and search_queries:
             valid_queries = [search_queries[0]]

        engine_name = self.browser.engine.capitalize()
        
        search_results = []
        if valid_queries:
            progress_callback(f"阶段 II: 在 {engine_name} 上搜索: {', '.join(valid_queries)}...")
            logger.info("[Workflow] 搜索引擎: %s, 查询: %s", engine_name, valid_queries)
            
            # [03] Web Search (Parallel) with timeout and retry
            search_tasks = [self.browser.search_web(q, log_func=progress_callback, session_id=self.session_id) for q in valid_queries]
            
            # First attempt with shorter timeout
            try:
                results_list = await asyncio.wait_for(asyncio.gather(*search_tasks, return_exceptions=True), timeout=60.0)
            except asyncio.TimeoutError:
                progress_callback("搜索超时（60秒），使用已获取的结果...")
                results_list = [[] for _ in search_tasks]
            
            # Handle exceptions from individual searches
            processed_results = []
            for i, result in enumerate(results_list):
                if isinstance(result, Exception):
                    logger.warning("[Workflow] 搜索查询 %d 失败: %s", i, result)
                    processed_results.append([])
                elif isinstance(result, list):
                    processed_results.append(result)
                else:
                    processed_results.append([])
            results_list = processed_results
            
            # Flatten, deduplicate and reindex results
            current_id = 1
            seen_result_urls = set()  # 用规范化后的 URL 去重
            for batch in results_list:
                for res in batch:
                    normalized = self._normalize_url(res.get('url', ''))
                    if normalized in seen_result_urls:
                        continue
                    seen_result_urls.add(normalized)
                    new_res = res.copy()
                    new_res['id'] = current_id
                    search_results.append(new_res)
                    current_id += 1
        else:
            if iteration > 1:
                progress_callback("警告: 模型建议的所有查询都已尝试过。")

        if not search_results:
            progress_callback("未找到搜索结果。")
            return [], source_id_counter, 0

        progress_callback(f"找到 {len(search_results)} 个结果。正在评估相关性...")
        progress_callback("正在调用 AI 评估搜索结果的相关性...")
        
        # [04] Relevance Assessment
        relevant_ids = await self.llm.assess_relevance(user_input, search_results)
        progress_callback(f"选定进行深度爬取的 ID: {relevant_ids}")
        
        # [05] Admission Filter — 优先未访问的 URL，如果都访问过则选次优结果
        to_crawl = []
        seen_urls_in_batch = set()
        already_visited = []

        for res in search_results:
            if res['id'] in relevant_ids:
                if res['url'] not in visited_urls and res['url'] not in seen_urls_in_batch:
                    to_crawl.append(res)
                    seen_urls_in_batch.add(res['url'])
                elif res['url'] in visited_urls:
                    already_visited.append(res)
        
        # 如果所有相关结果都已访问，从未访问的非相关结果中补充
        if not to_crawl:
            # 先尝试已访问的 URL 对应的搜索结果页面中是否有其他候选
            for res in search_results:
                if res['url'] not in visited_urls and res['url'] not in seen_urls_in_batch and res['id'] not in relevant_ids:
                    to_crawl.append(res)
                    seen_urls_in_batch.add(res['url'])
                    if len(to_crawl) >= 3:
                        break
        
        # [06] Deep Crawling
        if not to_crawl:
            progress_callback("未找到新的相关页面进行爬取 (可能已访问过)。")
            return [], source_id_counter, len(search_results)

        sources, counter = await self._crawl_and_collect(to_crawl, visited_urls, progress_callback,
                                              user_input, source_id_counter)
        return sources, counter, len(search_results)

    async def _crawl_and_collect(self, to_crawl: list, visited_urls: set,
                                   progress_callback: Callable[[str], None],
                                   user_input: str, source_id_counter: int) -> tuple:
        """批量爬取页面并收集结果，返回 (new_sources, source_id_counter)。支持内容缓存。"""
        progress_callback(f"正在并行爬取 {len(to_crawl)} 个页面...")
        for item in to_crawl:
            logger.info("[Workflow] 爬取: %s", item.get('url', '?')[:100])
            progress_callback(f"  → {item.get('title', item.get('url', '?'))[:60]}")

        # Separate cached vs uncached
        cached_contents: Dict[int, str] = {}
        uncached_items: list = []
        uncached_indices: list = []

        for i, item in enumerate(to_crawl):
            cached = self._content_cache.get(item['url'])
            if cached:
                cached_contents[i] = cached
                progress_callback(f"  ✓ 使用缓存: {item.get('title', '?')[:50]}")
            else:
                uncached_items.append(item)
                uncached_indices.append(i)

        # Crawl only uncached pages — limit concurrency to avoid API rate limits
        if uncached_items:
            # Use semaphore to limit parallel crawls (each crawl may trigger LLM calls)
            max_concurrent = min(self.max_concurrent_pages, len(uncached_items))
            semaphore = asyncio.Semaphore(max_concurrent)

            async def _crawl_with_semaphore(task):
                async with semaphore:
                    return await task

            tasks = [_crawl_with_semaphore(
                self.browser.crawl_page(item['url'], log_func=progress_callback, interactive_mode=self.interactive_search, query=user_input, llm_client=self.llm, session_id=self.session_id)
            ) for item in uncached_items]
            contents = await asyncio.gather(*tasks)
            # Build URL-to-index map for efficient lookup
            url_to_item = {item['url']: (idx, item) for idx, item in zip(uncached_indices, uncached_items)}
            # Cache results
            for content, (orig_idx, item) in zip(contents, url_to_item.values()):
                cached_contents[orig_idx] = content
                if content and content != "[CRAWL_TIMEOUT]":
                    self._content_cache[item['url']] = content

        new_sources = []
        for i, item in enumerate(to_crawl):
            visited_urls.add(item['url'])
            source_id_counter += 1
            content = cached_contents.get(i, "")
            content_len = len(content) if content else 0
            # 过滤超时/空内容
            if content == "[CRAWL_TIMEOUT]" or not content:
                logger.warning("[Workflow] 跳过无效页面: %s (len=%d)", item['url'][:80], content_len)
                progress_callback(f"跳过超时/空页面: {item['url']}")
                continue
            logger.info("[Workflow] 爬取成功: %s (len=%d)", item['url'][:80], content_len)
            # [07] Structure Data
            new_sources.append({
                "id": source_id_counter,
                "title": item['title'],
                "url": item['url'],
                "date": item.get('date', ''),
                "content": content
            })

        return new_sources, source_id_counter

    async def run(self, user_input: str, progress_callback: Callable[[str], None], stream_callback: Optional[Callable[[str], None]] = None, history: Optional[List[Dict[str, str]]] = None, source_callback: Optional[Callable[[List[Dict]], None]] = None, stats_callback: Optional[Callable[[Dict], None]] = None) -> str:
        """
        Executes the JustSearch Workflow with iterative refinement.
        """
        try:
            iteration = 0
            accumulated_sources = []
            visited_urls = set()
            search_history = []
            last_feedback = "" 
            source_id_counter = 0
            total_search_results = 0
            import time
            start_time = time.monotonic()
            _MAX_TOTAL_SECONDS = 180  # 3 minutes total timeout
            
            while iteration < self.max_iterations:
                iteration += 1
                # Total timeout check
                elapsed = time.monotonic() - start_time
                if elapsed > _MAX_TOTAL_SECONDS:
                    progress_callback(f"已超过总搜索时限 ({_MAX_TOTAL_SECONDS}秒)，正在整理现有结果...")
                    logger.info("[Workflow] 总搜索超时 (%.1fs), 已完成 %d 次迭代", elapsed, iteration - 1)
                    break
                if iteration == 1:
                    progress_callback(f"🔍 第 1 轮搜索开始...")
                else:
                    progress_callback(f"🔄 第 {iteration}/{self.max_iterations} 轮搜索开始...")
                logger.info("[Workflow] === 迭代 %d/%d 开始 ===", iteration, self.max_iterations)
                logger.info("[Workflow] 用户输入: %s", user_input[:100])
                
                # [02] Task Analysis
                if iteration == 1:
                    analysis_input = user_input
                else:
                    analysis_input = (
                        f"Original User Question: {user_input}\n"
                        f"Previous Search Queries Tried: {search_history}\n"
                        f"Reason previous results were insufficient: {last_feedback}\n"
                        f"Task: Generate a NEW, different search query (or specific URL) to find the missing information."
                    )
                
                # Pass conversation history to help understand context (e.g. "it", "he")
                analysis = await self.llm.analyze_task(analysis_input, history)
                logger.info("[Workflow] 任务分析结果: type=%s, data=%s", analysis.get("type", ""), json.dumps(analysis, ensure_ascii=False)[:150])
                
                new_sources = []
                
                if analysis.get("type") == "direct":
                    raw_url = analysis.get("url")
                    url = raw_url
                    new_sources, source_id_counter = await self._handle_direct_url(
                        url, visited_urls, progress_callback, user_input, source_id_counter
                    )
                else:
                    search_queries = analysis.get("queries", [])
                    # Fallback for single query or if model returns old format
                    if not search_queries and analysis.get("query"):
                        search_queries = [analysis.get("query")]
                    
                    new_sources, source_id_counter, search_count = await self._handle_search(
                        search_queries, search_history, visited_urls, iteration,
                        progress_callback, user_input, source_id_counter
                    )
                    total_search_results += search_count
                
                accumulated_sources.extend(new_sources)
                
                if not accumulated_sources:
                    progress_callback("目前尚未收集到有效信息，尝试下一次迭代...")
                    last_feedback = "No valid sources found yet."
                    continue

                # [09] Generation & Evaluation
                progress_callback(f"阶段 III: 使用累计 {len(accumulated_sources)} 个来源生成答案...")
                progress_callback("正在调用 AI 模型生成回答...")
                
                if source_callback:
                    source_callback(accumulated_sources)
                
                result = await self.llm.generate_answer(user_input, accumulated_sources, history, stream_callback)
                
                if result.get("status") == "sufficient":
                    progress_callback("答案状态: 充分")
                    final_answer = result.get("answer")
                    if stats_callback:
                        total_elapsed = time.monotonic() - start_time
                        stats_callback({
                            "sites_searched": total_search_results,
                            "sites_crawled": len(visited_urls),
                            "iterations": iteration,
                            "prompt_tokens": self.llm.total_prompt_tokens,
                            "completion_tokens": self.llm.total_completion_tokens,
                            "total_seconds": round(total_elapsed, 1),
                        })
                    return self._format_references(final_answer, accumulated_sources)
                else:
                    last_feedback = result.get("answer")
                    if iteration < self.max_iterations:
                        progress_callback(f"⚠️ 已有信息不足以完整回答，正在进行第 {iteration + 1} 轮深度搜索...")
                        # Include specific missing info in the feedback
                        missing = result.get("answer", "")[:200]
                        if missing:
                            progress_callback(f"缺失信息: {missing}")
                    else:
                        progress_callback(f"已达到最大迭代次数 ({self.max_iterations})，正在整理现有结果...")
                    
                    if iteration >= self.max_iterations:
                         final_answer = f"经过 {iteration} 次尝试后，我无法找到完全充分的答案。以下是基于现有信息的结果：\n\n{result.get('answer')}"
                         if stats_callback:
                             stats_callback({
                                 "sites_searched": total_search_results,
                                 "iterations": iteration,
                                 "total_seconds": round(time.monotonic() - start_time, 1),
                                 "prompt_tokens": self.llm.total_prompt_tokens,
                                 "completion_tokens": self.llm.total_completion_tokens,
                             })
                         return final_answer
            
            return "多次尝试后未能生成有效答案。建议您尝试：\n1. 换用不同的关键词重新提问\n2. 简化问题，分步骤提问\n3. 切换搜索引擎后重试"
            
        finally:
            pass
