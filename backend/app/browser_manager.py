import asyncio
import logging
import os
import random
import urllib.parse
import base64
import time
import json
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth
from typing import List, Dict

logger = logging.getLogger(__name__)

# Global browser state
_GLOBAL_PLAYWRIGHT = None
_GLOBAL_CONTEXT = None
_CURRENT_HEADLESS_MODE = True
_SEARCH_LOCK = asyncio.Lock()
_LAST_REQUEST_TIME = 0
_MIN_SEARCH_INTERVAL = 4.0  # Minimum seconds between search requests

# Store pages that need user interaction: session_id -> { "page": page, "event": asyncio.Event() }
_INTERACTION_SESSIONS = {}

def get_interaction_session(session_id: str):
    return _INTERACTION_SESSIONS.get(session_id)

async def mark_interaction_completed(session_id: str):
    if session_id in _INTERACTION_SESSIONS:
        _INTERACTION_SESSIONS[session_id]["event"].set()

# List of modern Chrome User Agents for macOS/Windows to rotate
CHROME_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.120 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

def get_browser_config(user_data_dir: str) -> Dict:
    """Get or create persistent browser configuration (UA, viewport)"""
    config_path = os.path.join(user_data_dir, "browser_config.json")
    config = {}
    
    # Try to load existing config
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            logger.error("Error loading browser config: %s", e)

    # Generate missing fields
    if "user_agent" not in config:
        config["user_agent"] = random.choice(CHROME_USER_AGENTS)
    
    if "viewport" not in config:
        config["viewport"] = {
            "width": 1280 + random.randint(0, 100),
            "height": 720 + random.randint(0, 100)
        }
        
    # Save config back
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error("Error saving browser config: %s", e)
        
    return config

async def init_global_browser(headless_override: bool = None):
    """Initializes the global browser instance."""
    global _GLOBAL_PLAYWRIGHT, _GLOBAL_CONTEXT, _CURRENT_HEADLESS_MODE
    
    if _GLOBAL_CONTEXT:
        return

    _GLOBAL_PLAYWRIGHT = await async_playwright().start()
    
    # Calculate project root from current file: backend/app/browser_manager.py -> ... -> ... -> root
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    user_data_dir = os.path.join(project_root, "user_data")
    if not os.path.exists(user_data_dir):
        os.makedirs(user_data_dir)
        
    # Get persistent browser config
    browser_config = get_browser_config(user_data_dir)
    user_agent = browser_config["user_agent"]
    width = browser_config["viewport"]["width"]
    height = browser_config["viewport"]["height"]
    
    # Determine headless mode from environment variable (default: True)
    if headless_override is not None:
        headless_mode = headless_override
    else:
        headless_mode = os.getenv("HEADLESS", "true").lower() == "true"
    
    _CURRENT_HEADLESS_MODE = headless_mode

    try:
        _GLOBAL_CONTEXT = await _GLOBAL_PLAYWRIGHT.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",  # Use real Chrome if available
            headless=headless_mode,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-extensions',
                '--window-size=1920,1080',
            ],
            viewport={"width": width, "height": height},
            user_agent=user_agent,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            ignore_default_args=["--enable-automation"],
        )
    except Exception as e:
        logger.warning("Failed to launch Chrome: %s. Trying default Chromium...", e)
        _GLOBAL_CONTEXT = await _GLOBAL_PLAYWRIGHT.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless_mode,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox',
                '--window-size=1920,1080',
            ],
            viewport={"width": width, "height": height},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=user_agent,
            ignore_default_args=["--enable-automation"]
        )
    logger.info("Global Browser Initialized with UA: %s", user_agent)

async def shutdown_global_browser():
    """Shuts down the global browser instance."""
    global _GLOBAL_PLAYWRIGHT, _GLOBAL_CONTEXT
    if _GLOBAL_CONTEXT:
        await _GLOBAL_CONTEXT.close()
        _GLOBAL_CONTEXT = None
    if _GLOBAL_PLAYWRIGHT:
        await _GLOBAL_PLAYWRIGHT.stop()
        _GLOBAL_PLAYWRIGHT = None
    logger.info("Global Browser Shutdown.")

async def get_new_page() -> Page:
    """Creates a new page, restarting the browser if the context is closed."""
    global _GLOBAL_CONTEXT
    
    if not _GLOBAL_CONTEXT:
        await init_global_browser()
        
    try:
        return await _GLOBAL_CONTEXT.new_page()
    except Exception as e:
        if "Target page, context or browser has been closed" in str(e):
            logger.warning("Browser context error: %s. Restarting browser...", e)
            await shutdown_global_browser()
            # Restore previous headless mode
            await init_global_browser(headless_override=_CURRENT_HEADLESS_MODE)
            return await _GLOBAL_CONTEXT.new_page()
        raise e

class BrowserManager:
    def __init__(self, engine: str = "duckduckgo", max_results: int = 8):
        self.stealth = Stealth()
        self.engine = engine
        self.max_results = max_results
        # Search Engine Configuration
        self.engine_config = self._load_selectors()

    def _load_selectors(self):
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'search_selectors.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error("Error loading search selectors: %s", e)
        
        # Fallback default
        return {
            "duckduckgo": {
                "base_url": "https://duckduckgo.com/?q={query}",
                "selectors": {
                    "result_container": ["article[data-testid='result']", ".react-results--main li"],
                    "title": "h2",
                    "link": "a[data-testid='result-title-a']",
                    "snippet": "[data-testid='result-snippet']",
                    "date": ".result__timestamp"
                },
                "captcha_check": [],
                "wait_selector": "#react-layout, .react-results--main"
            }
        }

    async def start(self):
        # Ensure global browser is running (idempotent check)
        if not _GLOBAL_CONTEXT:
            await init_global_browser()

    async def stop(self):
        # Do not stop global browser as it is shared
        pass

    async def search_web(self, query: str, log_func=None, session_id: str = None) -> List[Dict]:
        """
        [03] Concurrent Web Search (Google/Bing)
        Scrapes search results for the query based on the selected engine.
        """
        if not _GLOBAL_CONTEXT:
            await self.start()
        
        # Get config based on current engine (default to duckduckgo if not found)
        config = self.engine_config.get(self.engine, self.engine_config["duckduckgo"])
        engine_name = self.engine.capitalize()

        page = await get_new_page()
        await self.stealth.apply_stealth_async(page)
        
        try:
            if log_func: log_func(f"浏览器: 正在前往 {engine_name} 搜索 '{query}'...")
            
            delay = random.uniform(1.0, 2.0)
            await asyncio.sleep(delay)
            
            # Construct URL
            encoded_query = urllib.parse.quote(query)
            # Google supports num param, others ignore it usually or we handle it differently
            url = config["base_url"].format(query=encoded_query, num=self.max_results + 2) # Request a few more to be safe
            # Enforce rate limiting to avoid CAPTCHAs
            global _LAST_REQUEST_TIME
            async with _SEARCH_LOCK:
                now = time.time()
                elapsed = now - _LAST_REQUEST_TIME
                if elapsed < _MIN_SEARCH_INTERVAL:
                    # Add some randomness to the wait
                    wait_time = _MIN_SEARCH_INTERVAL - elapsed + random.uniform(0.5, 1.5)
                    if log_func: log_func(f"浏览器: 正在排队等待搜索 (冷却 {wait_time:.1f}s)...")
                    await asyncio.sleep(wait_time)
                
                _LAST_REQUEST_TIME = time.time()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    
                    # Human-like interaction: Random mouse movement and scrolling
                    try:
                        await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                    except Exception:
                        pass
                except Exception as e:
                    if log_func: log_func(f"浏览器: 搜索页面加载失败: {e}")
                    return []

            # Check for CAPTCHA (Mainly for Google)
            try:
                content = await page.content()
            except Exception:
                content = ""
                
            detected_captcha = False
            for check in config["captcha_check"]:
                if check.startswith("#"):
                    try:
                        if await page.query_selector(check):
                            detected_captcha = True
                            break
                    except Exception:
                        pass
                elif check in content:

            if detected_captcha:
                logger.warning("CAPTCHA detected on %s!", engine_name)
                if log_func: log_func("浏览器: 检测到验证码！等待手动解决...")
                
                if session_id:
                     if log_func: log_func("ACTION_REQUIRED: CAPTCHA_DETECTED") # Special signal for frontend
                     
                     # Register session for interaction
                     event = asyncio.Event()
                     _INTERACTION_SESSIONS[session_id] = {
                        "page": page,
                        "event": event,
                        "last_active": time.time()
                     }
                     
                     if log_func: log_func(f"浏览器: 请点击界面上的'手动验证'按钮来解决验证码")
                     
                     # Wait for completion signal or timeout (3 minutes)
                     try:
                        # Wait for either event set or wait selector success (in case user solves it but forgets to click done)
                        # But here we mainly rely on user clicking "Done" in our UI or the event being set
                        await asyncio.wait_for(event.wait(), timeout=600.0)
                        if log_func: log_func("浏览器: 收到验证完成信号，继续执行...")
                     except asyncio.TimeoutError:
                        if log_func: log_func("浏览器: 等待手动验证超时 (10分钟)。")
                     finally:
                        if session_id in _INTERACTION_SESSIONS:
                            del _INTERACTION_SESSIONS[session_id]
                else:
                    # Fallback old logic
                    try:
                        await page.wait_for_selector(config["wait_selector"], timeout=60000)
                        if log_func: log_func("浏览器: 验证码已解决，继续...")
                    except asyncio.TimeoutError:
                        if log_func: log_func("浏览器: 验证码未及时解决。")
            
            # Wait for results
            try:
                # Increased timeout to 30s for slower loads after CAPTCHA
                await page.wait_for_selector(config["wait_selector"], timeout=30000)
                await asyncio.sleep(1.0) # Let the page settle
            except Exception as e:
                msg = f"等待结果容器 ({config['wait_selector']}) 超时。"
                logger.warning(msg)
                if log_func: log_func(f"浏览器错误: {msg}")
                return []

            if log_func: log_func(f"浏览器: 正在解析结果...")

            # Retry logic for evaluate
            results = []
            for attempt in range(3):
                try:
                    # Extract results using page.evaluate for safety and speed
                    results = await page.evaluate("""([selectors, max_results]) => {
                        const results = [];
                        let count = 0;
                        
                        // Try to find container elements
                        let elements = [];
                        for (const sel of selectors.result_container) {
                            const found = document.querySelectorAll(sel);
                            if (found && found.length > 0) {
                                elements = Array.from(found);
                                break;
                            }
                        }
                        
                        if (elements.length === 0) return results;
                        
                        for (const el of elements) {
                            if (count >= max_results) break;
                            
                            const titleEl = el.querySelector(selectors.title);
                            const linkEl = el.querySelector(selectors.link);
                            const snippetEl = el.querySelector(selectors.snippet);
                            const dateEl = selectors.date ? el.querySelector(selectors.date) : null;
                            
                            if (!titleEl || !linkEl) continue;
                            
                            const title = titleEl.innerText;
                            const url = linkEl.href;
                            let snippet = "";
                            let date = "";
                            
                            if (dateEl) {
                                date = dateEl.innerText;
                            }
                            
                            if (snippetEl) {
                                snippet = snippetEl.innerText;
                            } else {
                                // Fallback snippet extraction
                                let text = el.innerText;
                                if (text.includes(title)) text = text.replace(title, "");
                                snippet = text.trim().substring(0, 200);
                            }

                            // Fallback: Check if date is at the start of snippet (e.g. "3 days ago — ...")
                            if (!date && snippet) {
                                const dateMatch = snippet.match(/^([a-zA-Z]{3} \d{1,2}, \d{4}|\d{1,2} [a-zA-Z]{3} \d{4}|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2} hours? ago|\d{1,2} days? ago)/);
                                if (dateMatch) {
                                    date = dateMatch[0];
                                }
                            }
                            
                            if (url && url.startsWith('http')) {
                                count++;
                                results.push({
                                    id: count,
                                    title: title,
                                    url: url,
                                    snippet: snippet,
                                    date: date
                                });
                            }
                        }
                        return results;
                    }""", [config["selectors"], self.max_results])
                    
                    # If successful, break out of retry loop
                    break
                except Exception as e:
                    if "Execution context was destroyed" in str(e) or "Cannot find context" in str(e):
                        if attempt < 2:
                            if log_func: log_func(f"浏览器: 页面上下文丢失，正在重试解析 ({attempt+1}/3)...")
                            await asyncio.sleep(1.0)
                            continue
                    # Re-raise if not a context issue or out of retries
                    raise e
            
            if log_func: log_func(f"浏览器: 成功解析 {len(results)} 个结果。")
            return results
        except Exception as e:
            msg = f"搜索错误: {e}"
            logger.error(msg)
            if log_func: log_func(f"浏览器错误: {msg}")
            return []
        finally:
            await page.close()

    async def crawl_page(self, url: str, log_func=None, interactive_mode: bool = False, query: str = None, llm_client=None, session_id: str = None) -> str:
        """
        [06] Headless Browser Deep Crawling
        """
        if not _GLOBAL_CONTEXT:
            await self.start()
            
        # Handle search engine redirect URLs (Bing/Google/DuckDuckGo)
        final_url = url
        if "bing.com/ck/a" in url or "google.com/url" in url or "duckduckgo.com/l/" in url:
            if log_func: log_func(f"浏览器: 检测到重定向 URL，正在尝试提取目标...")
            
            # Handle DuckDuckGo
            if "duckduckgo.com/l/" in url:
                try:
                    parsed = urllib.parse.urlparse(url)
                    params = urllib.parse.parse_qs(parsed.query)
                    if 'uddg' in params:
                        final_url = params['uddg'][0]
                        if log_func: log_func(f"浏览器: 提取 DuckDuckGo 重定向 URL 成功: {final_url}")
                except Exception as e:
                    if log_func: log_func(f"浏览器: 提取 DuckDuckGo 重定向 URL 失败: {e}")

            # For Bing, the 'u' parameter is often base64 encoded with 'a1' prefix
            elif "bing.com/ck/a" in url:
                parsed = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed.query)
                if 'u' in params:
                    u_val = params['u'][0]
                    if u_val.startswith('a1'):
                        try:
                            # a1 prefix, then base64
                            b64_part = u_val[2:]
                            # Add padding if needed
                            b64_part += "=" * ((4 - len(b64_part) % 4) % 4)
                            decoded = base64.b64decode(b64_part).decode('utf-8')
                            final_url = decoded
                            if log_func: log_func(f"浏览器: 提取 Bing 重定向 URL 成功: {final_url}")
                        except Exception as e:
                            if log_func: log_func(f"浏览器: 提取 Bing 重定向 URL 失败: {e}")

        page = await get_new_page()
        await self.stealth.apply_stealth_async(page)

        try:
            if log_func: log_func(f"浏览器: 正在爬取 {final_url}...")
            
            # Special handling for GitHub API requests to make them useful for LLM
            if "api.github.com" in final_url and "/repos" in final_url:
                if log_func: log_func(f"浏览器: 检测到 GitHub API 请求，正在优化数据...")
                try:
                    await page.goto(final_url, wait_until="networkidle", timeout=30000)
                    json_content = await page.evaluate("() => document.body.innerText")
                    try:
                        data = json.loads(json_content)
                        if isinstance(data, list):
                            # Summarize repository list
                            summary = f"GitHub API Repository List Summary (First 30 items):\n"
                            total_stars = 0
                            for repo in data:
                                name = repo.get("name", "Unknown")
                                stars = repo.get("stargazers_count", 0)
                                desc = repo.get("description", "")
                                total_stars += stars
                                summary += f"- {name}: {stars} stars ({desc})\n"
                            
                            summary += f"\nTotal stars in this page: {total_stars}\n"
                            
                            # Check for pagination
                            # GitHub API uses Link header for pagination, but we can't easily access headers in page.goto
                            # However, we can try to guess if there are more by checking if we got 30 items
                            if len(data) == 30:
                                summary += "WARNING: There are likely more repositories (pagination detected). This count is INCOMPLETE.\n"
                            
                            if log_func: log_func(f"浏览器: 成功解析 GitHub API 数据，当前页共 {total_stars} stars。")
                            return summary
                    except json.JSONDecodeError:
                        pass
                except Exception as e:
                    if log_func: log_func(f"浏览器: GitHub API 处理失败: {e}")

            try:
                await page.goto(final_url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                if log_func: log_func(f"浏览器: 加载页面超时或失败 {final_url}: {e}")
                return ""

            # Try to wait for content to stabilize
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
                # Specific wait for GitHub repository lists
                if "github.com" in final_url and "tab=repositories" in final_url:
                    try:
                        await page.wait_for_selector("#user-repositories-list", timeout=5000)
                        
                        # Inject JS to extract star counts directly from the DOM
                        repo_stats = await page.evaluate("""() => {
                            let totalStars = 0;
                            let repos = [];
                            
                            // Select all repository items
                            const items = document.querySelectorAll('li[itemprop="owns"], .source, .public');
                            
                            items.forEach(item => {
                                // Try to find star count
                                // Usually in an 'a' tag with 'stargazers' in href, or just by icon
                                const starLink = item.querySelector('a[href*="/stargazers"]');
                                if (starLink) {
                                    const text = starLink.innerText.trim().replace(/,/g, '');
                                    const stars = parseInt(text);
                                    if (!isNaN(stars)) {
                                        totalStars += stars;
                                        
                                        // Get repo name
                                        const nameEl = item.querySelector('a[itemprop="name codeRepository"], h3 a');
                                        const name = nameEl ? nameEl.innerText.trim() : "Unknown";
                                        
                                        repos.push({name, stars});
                                    }
                                }
                            });
                            
                            return {totalStars, repos, count: repos.length};
                        }""")
                        
                        if repo_stats and repo_stats.get('count', 0) > 0:
                            if log_func: log_func(f"浏览器: 页面内统计到 {repo_stats['count']} 个仓库，共 {repo_stats['totalStars']} stars。")
                            
                            # Prepend this high-value information to the content
                            prepend_text = f"--- AUTOMATED ANALYSIS ---\n"
                            prepend_text += f"Total Stars visible on this page: {repo_stats['totalStars']}\n"
                            prepend_text += f"Repository Count visible: {repo_stats['count']}\n"
                            prepend_text += f"Top Repositories (First few):\n"
                            for r in repo_stats['repos']:
                                prepend_text += f"- {r['name']}: {r['stars']} stars\n"
                            prepend_text += f"--------------------------\n\n"
                            
                            page.prepend_text = prepend_text
                        else:
                            if log_func: log_func("浏览器: 未能在页面上提取到 Star 数据。")
                            
                    except Exception as e:
                         if log_func: log_func(f"浏览器: GitHub 页面分析失败: {e}")
            except Exception:
                pass

            # --- Interactive Mode ---
            if interactive_mode and query and llm_client:
                try:
                    if log_func: log_func("浏览器: 交互模式已开启，正在提取可点击元素...")
                    
                    # 1. Extract clickable elements (buttons, links with interesting text)
                    # We inject a script to find elements
                    elements = await page.evaluate("""() => {
                        const items = [];
                        let idCounter = 0;
                        
                        // Helper to check if element is visible
                        function isVisible(elem) {
                            if (!elem.getBoundingClientRect || !elem.checkVisibility) return false;
                            const rect = elem.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0 && elem.checkVisibility();
                        }

                        // Collect buttons and links
                        const candidates = document.querySelectorAll('button, a[href], [role="button"]');
                        
                        for (const el of candidates) {
                            if (!isVisible(el)) continue;
                            
                            const text = el.innerText.trim();
                            if (text.length < 2 || text.length > 50) continue; // Filter too short/long
                            
                            // Filter common noise
                            if (/^(home|login|sign in|sign up|menu|privacy|terms)$/i.test(text)) continue;
                            
                            // Assign a temp ID attribute to locate it later
                            const tempId = "js-interact-" + idCounter++;
                            el.setAttribute("data-js-interact-id", tempId);
                            
                            items.push({
                                id: tempId,
                                text: text,
                                tag: el.tagName.toLowerCase()
                            });
                            
                            if (items.length >= 50) break; // Limit count
                        }
                        return items;
                    }""")
                    
                    if elements:
                        if log_func: log_func(f"浏览器: 提取到 {len(elements)} 个候选元素。请求 AI 决策...")
                        
                        # 2. Ask LLM what to click
                        clicked_ids = await llm_client.decide_click_elements(query, elements)
                        
                        if clicked_ids:
                            if log_func: log_func(f"浏览器: AI 决定点击元素 ID: {clicked_ids}")
                            
                            for cid in clicked_ids:
                                try:
                                    # Locate by the temp ID we injected
                                    await page.click(f'[data-js-interact-id="{cid}"]', timeout=2000)
                                    if log_func: log_func(f"浏览器: 已点击元素 {cid}")
                                    await asyncio.sleep(1.0) # Wait for reaction
                                except Exception as e:
                                    if log_func: log_func(f"浏览器: 点击元素 {cid} 失败: {e}")
                            
                            # Wait for potential new content
                            try:
                                await page.wait_for_load_state("networkidle", timeout=3000)
                            except Exception:
                                await asyncio.sleep(2.0)
                        else:
                            if log_func: log_func("浏览器: AI 决定不点击任何元素。")
                    else:
                        if log_func: log_func("浏览器: 未找到显著的可交互元素。")
                        
                except Exception as e:
                    if log_func: log_func(f"浏览器: 交互模式执行出错: {e}")

            # --- End Interactive Mode ---

            content = ""
            # Retry logic for execution context issues
            for attempt in range(3):
                try:
                    content = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    break
                except Exception as e:
                    if "Execution context was destroyed" in str(e) or "Cannot find context" in str(e):
                        if attempt < 2:
                            # If context destroyed, it means a navigation happened. 
                            # We should wait for the new page to load.
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                            except Exception:
                                await asyncio.sleep(2) # Fallback sleep
                            continue
                    # For other errors, we might want to log but return empty string rather than crashing
                    logger.error("Extraction error on %s: %s", url, e)
                    break
            
            content_len = len(content)
            
            # Prepend analysis if available
            if hasattr(page, 'prepend_text'):
                content = page.prepend_text + content
                content_len = len(content)
            
            if log_func: log_func(f"浏览器: 已爬取 {url} - 提取了 {content_len} 个字符。")
            return content.strip()
            
        except Exception as e:
            msg = f"Crawl error for {url}: {e}"
            logger.error(msg)
            if log_func: log_func(f"浏览器错误: {msg}")
            return f"爬取页面时出错: {str(e)}"
        finally:
            await page.close()