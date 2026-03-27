import asyncio
from typing import List, Dict, Callable, Any, Optional
from .llm_client import LLMClient
from .browser_manager import BrowserManager

class SearchWorkflow:
    def __init__(self, api_key: str, base_url: str, model: str, search_engine: str = "duckduckgo", max_results: int = 8, max_iterations: int = 5, interactive_search: bool = True, session_id: str = None, max_context_turns: int = 6):
        self.llm = LLMClient(api_key, base_url, model, max_context_turns=max_context_turns)
        # Pass the search engine preference to the browser manager
        self.browser = BrowserManager(engine=search_engine, max_results=max_results)
        self.max_iterations = max_iterations
        self.history = []
        self.interactive_search = interactive_search
        self.session_id = session_id

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
            url = src.get('url', '#')
            date = src.get('date', '')
            date_str = f" ({date})" if date else ""
            ref_section += f"[{src['id']}] [{title}]({url}){date_str}  \n" 
            
        return answer + ref_section

    async def run(self, user_input: str, progress_callback: Callable[[str], None], stream_callback: Optional[Callable[[str], None]] = None, history: Optional[List[Dict[str, str]]] = None, source_callback: Optional[Callable[[List[Dict]], None]] = None) -> str:
        """
        Executes the JustSearch Workflow with iterative refinement.
        """
        # Browser is managed globally, no need to start/stop here
        # await self.browser.start()
        
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
                
                new_sources = []
                
                if analysis.get("type") == "direct":
                    raw_url = analysis.get("url")
                    url = raw_url
                    
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
                            progress_callback(f"检测到 GitHub 用户主页，正在优化 URL 以获取仓库列表...")
                            url = f"{url}?tab=repositories&q=&type=&language=&sort=stargazers"

                    progress_callback(f"目标 URL: {url}")
                    
                    if url not in visited_urls:
                        content = await self.browser.crawl_page(url, log_func=progress_callback, interactive_mode=self.interactive_search, query=user_input, llm_client=self.llm, session_id=self.session_id)
                        visited_urls.add(url)
                        source_id_counter += 1
                        # 过滤超时/空内容
                        if content == "[CRAWL_TIMEOUT]" or not content:
                            progress_callback(f"跳过超时/空页面: {url}")
                            continue
                        new_sources.append({
                            "id": source_id_counter, 
                            "url": url, 
                            "title": "Direct URL", 
                            "content": content
                        })
                    else:
                        progress_callback(f"URL 已访问过，跳过: {url}")
                        
                else:
                    search_queries = analysis.get("queries", [])
                    # Fallback for single query or if model returns old format
                    if not search_queries and analysis.get("query"):
                        search_queries = [analysis.get("query")]
                    
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
                    
                    if valid_queries:
                        progress_callback(f"阶段 II: 在 {engine_name} 上搜索: {', '.join(valid_queries)}...")
                        
                        # [03] Web Search (Parallel)
                        tasks = [self.browser.search_web(q, log_func=progress_callback, session_id=self.session_id) for q in valid_queries]
                        results_list = await asyncio.gather(*tasks)
                        
                        # Flatten and Reindex results
                        search_results = []
                        current_id = 1
                        for batch in results_list:
                            for res in batch:
                                new_res = res.copy()
                                new_res['id'] = current_id
                                search_results.append(new_res)
                                current_id += 1
                    else:
                        search_results = []
                        if iteration > 1:
                            progress_callback("警告: 模型建议的所有查询都已尝试过。")

                    if not search_results:
                        progress_callback("未找到搜索结果。")
                    else:
                        progress_callback(f"找到 {len(search_results)} 个结果。正在评估相关性...")
                        
                        # [04] Relevance Assessment
                        # Use user_input as the query context for relevance assessment to cover all aspects
                        relevant_ids = await self.llm.assess_relevance(user_input, search_results)
                        progress_callback(f"选定进行深度爬取的 ID: {relevant_ids}")
                        
                        # [05] Admission Filter
                        to_crawl = []
                        seen_urls_in_batch = set()

                        for res in search_results:
                            if res['id'] in relevant_ids:
                                if res['url'] not in visited_urls and res['url'] not in seen_urls_in_batch:
                                    to_crawl.append(res)
                                    seen_urls_in_batch.add(res['url'])
                                else:
                                    pass 
                        
                        # [06] Deep Crawling
                        if not to_crawl:
                            progress_callback("未找到新的相关页面进行爬取 (可能已访问过)。")
                        else:
                            progress_callback(f"正在爬取 {len(to_crawl)} 个新页面...")
                            tasks = [self.browser.crawl_page(item['url'], log_func=progress_callback, interactive_mode=self.interactive_search, query=user_input, llm_client=self.llm, session_id=self.session_id) for item in to_crawl]
                            contents = await asyncio.gather(*tasks)
                            
                            for i, item in enumerate(to_crawl):
                                visited_urls.add(item['url'])
                                source_id_counter += 1
                                content = contents[i]
                                # 过滤超时/空内容
                                if content == "[CRAWL_TIMEOUT]" or not content:
                                    progress_callback(f"跳过超时/空页面: {item['url']}")
                                    continue
                                # [07] Structure Data
                                new_sources.append({
                                    "id": source_id_counter, 
                                    "title": item['title'],
                                    "url": item['url'],
                                    "date": item.get('date', ''),
                                    "content": content
                                })
                
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
                    
                    # Stream the references if we have a callback
                    if stream_callback:
                        refs = formatted_result[len(final_answer):]
                        if refs:
                            stream_callback(refs)
                            
                    return formatted_result
                else:
                    last_feedback = result.get("answer")
                    progress_callback(f"答案状态: 不充分 (迭代 {iteration}/{self.max_iterations})")
                    progress_callback(f"原因/缺失信息: {last_feedback}")
                    
                    if iteration >= self.max_iterations:
                         final_answer = f"经过 {iteration} 次尝试后，我无法找到完全充分的答案。以下是基于现有信息的结果：\n\n{result.get('answer')}"
                         if stream_callback:
                             stream_callback(final_answer)
                         return self._format_references(final_answer, accumulated_sources)
            
            return "多次尝试后未能生成有效答案。"
            
        finally:
            # await self.browser.stop()
            pass