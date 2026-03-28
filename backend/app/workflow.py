import asyncio
import asyncio
import base64
import json
import logging
from typing import List, Dict, Callable, Any, Optional
from .llm_client import LLMClient
from .browser_manager import BrowserManager

logger = logging.getLogger(__name__)

class SearchWorkflow:
    def __init__(self, api_key: str, base_url: str, model: str, search_engine: str = "duckduckgo", max_results: int = 8, max_iterations: int = 5, interactive_search: bool = True, session_id: str = None, max_context_turns: int = 6):
        self.llm = LLMClient(api_key, base_url, model, max_context_turns=max_context_turns)
        # Pass the search engine preference to the browser manager
        self.browser = BrowserManager(engine=search_engine, max_results=max_results)
        self.max_iterations = max_iterations
        self.history = []
        self.interactive_search = interactive_search
        self.session_id = session_id

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
        
        unique_sources = {}
        for src in sources:
            unique_sources[src['id']] = src
        
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
        # Heuristic Optimization for GitHub User Profiles
        # If the task is about counting stars or repos, and the URL is a user profile,
        # automatically redirect to the repositories tab sorted by stargazers.
        if "github.com" in url and "tab=" not in url:
            # Check if it looks like a user profile (e.g. github.com/username)
            # Remove protocol
            clean_url = url.replace("https://", "").replace("http://", "").rstrip('/')
            parts = clean_url.split('/')
            
            if len(parts) == 2 and parts[1] not in ["login", "search", "explore", "topics", "about", "pricing"]:
                # It might be a user or org profile
                progress_callback("检测到 GitHub 用户主页，正在优化 URL 以获取仓库列表...")
                url = f"{url}?tab=repositories&q=&type=&language=&sort=stargazers"

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
        """处理搜索引擎查询，返回 (new_sources, source_id_counter)。"""
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
            
            # [03] Web Search (Parallel)
            tasks = [self.browser.search_web(q, log_func=progress_callback, session_id=self.session_id) for q in valid_queries]
            results_list = await asyncio.gather(*tasks)
            
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
            return [], source_id_counter

        progress_callback(f"找到 {len(search_results)} 个结果。正在评估相关性...")
        
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
            return [], source_id_counter

        return await self._crawl_and_collect(to_crawl, visited_urls, progress_callback,
                                              user_input, source_id_counter)

    async def _crawl_and_collect(self, to_crawl: list, visited_urls: set,
                                   progress_callback: Callable[[str], None],
                                   user_input: str, source_id_counter: int) -> tuple:
        """批量爬取页面并收集结果，返回 (new_sources, source_id_counter)。"""
        progress_callback(f"正在爬取 {len(to_crawl)} 个新页面...")
        for item in to_crawl:
            logger.info("[Workflow] 爬取: %s", item.get('url', '?')[:100])
        tasks = [self.browser.crawl_page(item['url'], log_func=progress_callback, interactive_mode=self.interactive_search, query=user_input, llm_client=self.llm, session_id=self.session_id) for item in to_crawl]
        contents = await asyncio.gather(*tasks)
        
        new_sources = []
        for i, item in enumerate(to_crawl):
            visited_urls.add(item['url'])
            source_id_counter += 1
            content = contents[i]
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

    async def run(self, user_input: str, progress_callback: Callable[[str], None], stream_callback: Optional[Callable[[str], None]] = None, history: Optional[List[Dict[str, str]]] = None, source_callback: Optional[Callable[[List[Dict]], None]] = None) -> str:
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
            
            while iteration < self.max_iterations:
                iteration += 1
                progress_callback(f"阶段 I: 分析任务 (第 {iteration} 次迭代)...")
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
                    
                    new_sources, source_id_counter = await self._handle_search(
                        search_queries, search_history, visited_urls, iteration,
                        progress_callback, user_input, source_id_counter
                    )
                
                accumulated_sources.extend(new_sources)
                
                if not accumulated_sources:
                    progress_callback("目前尚未收集到有效信息，尝试下一次迭代...")
                    last_feedback = "No valid sources found yet."
                    continue

                # [09] Generation & Evaluation
                progress_callback(f"阶段 III: 使用累计 {len(accumulated_sources)} 个来源生成答案...")
                
                if source_callback:
                    source_callback(accumulated_sources)
                
                result = await self.llm.generate_answer(user_input, accumulated_sources, history, stream_callback)
                
                if result.get("status") == "sufficient":
                    progress_callback("答案状态: 充分")
                    final_answer = result.get("answer")
                    formatted_result = self._format_references(final_answer, accumulated_sources)
                    return formatted_result
                else:
                    last_feedback = result.get("answer")
                    progress_callback(f"答案状态: 不充分 (迭代 {iteration}/{self.max_iterations})")
                    progress_callback(f"原因/缺失信息: {last_feedback}")
                    
                    if iteration >= self.max_iterations:
                         final_answer = f"经过 {iteration} 次尝试后，我无法找到完全充分的答案。以下是基于现有信息的结果：\n\n{result.get('answer')}"
                         return self._format_references(final_answer, accumulated_sources)
            
            return "多次尝试后未能生成有效答案。"
            
        finally:
            pass
