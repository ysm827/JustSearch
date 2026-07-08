# Changelog

## [2.2.0] - 2026-07-09

### Changed
- **浏览器桥接架构全面落地**:用自建 Chrome 扩展(MV3 + `chrome.debugger` CDP)+ 本地 WebSocket 桥接驱动你**真实**的 Chrome,取代旧版无头浏览器池。直接复用登录态/Cookie,验证码显著减少。新增 `extension/`(扩展源码)与 `backend/app/extension_bridge.py`(WS 服务端 + JSON-RPC 客户端 + tab 生命周期)。
- **彻底移除 Playwright**:删除 `browser_context.py`、`interaction.py`、`tools/manual_login.py`、`browser-modal.js`;`requirements.txt` 不再含 Playwright,Docker 镜像不再打包 Chromium。
- **Dockerfile** 移除 `COPY tools/`(已无该目录),镜像更小、构建更快。
- **JS/CSS 静态资源** 改为 `public, max-age=3600` 缓存(带 `?v=` 版本号失效),减少刷新请求。

### Removed(按需精简保护机制)
- **搜索冷却**(`rate_limit.py` / `search_rate_limit` ≥4s 串行排队):已删除,搜索不再强制间隔。
- **Chrome 标签并发上限**(`BRIDGE_MAX_CONCURRENT_PAGES` / `TabPool` semaphore / `max_concurrent_pages` 设置项):已删除,后台标签与爬取不再限并发。
- **搜索引擎健康度与自动降级**(`engine_health.py` / `get_fallback` / `batch_id`):已删除,不再因失败率自动切换引擎。
- **Chat 接口限流**(`rate_limiter.py` / `chat_limiter` 30 次/分钟):已删除,对话接口不再返回 429。

### Fixed
- 恢复 `search_web` 的搜索结果缓存读取(冷却移除时误删,已补回)。

### Notes
- `/api/health` 不再返回 `engines` 健康度字段。
- 环境变量 `BRIDGE_MAX_CONCURRENT_PAGES` 与设置项 `max_concurrent_pages` 已废弃,相关 UI 输入框同步移除。

## [2.1.0] - 2026-03-29

### Added
- **Brave Search** 和 **SearXNG** 搜索引擎支持
- **FTS5 全文搜索** API (`/api/history/search?q=`)
- **API 密钥验证** 端点 (`/api/settings/validate-key`)
- **搜索引擎列表** 端点 (`/api/engines`)
- **对话链接分享** 按钮
- **导出全部对话** 按钮
- **Ctrl+/** 快捷键切换侧栏
- **Ctrl+Shift+R** 快捷键重新生成回答
- **模型选择持久化** (localStorage)
- **自动对话标题生成** (基于首条消息)
- 内容提取器: 知乎、微信公众号、GitHub、B站、小红书
- Cloudflare 验证页面检测
- 搜索结果权威域名排序 (Wikipedia, GitHub, SO 等)
- 自适应搜索深度 (内容不足时自动增加轮次)
- LLM 任务分析结果缓存 (TTL 3分钟)
- generate_answer 流式请求并发控制和超时重试
- 请求计时中间件 (X-Response-Time header)
- 请求 ID 追踪 (X-Request-ID header)
- GZip 压缩中间件
- 健康检查添加 uptime 和内存使用量
- 前端建议模板更新 (emoji + 当前话题)

### Fixed
- browser_context.py `_GLOBAL_CONTEXT` 未定义引用
- 删除 stale `.patch.2` 文件

### Improved
- DuckDuckGo 选择器增加更多备选
- prompt 改进: 评测/教程查询建议、对比结构化输出
- 历史搜索支持分词匹配
- 搜索空状态添加图标和友好提示
- model 逗号分隔解析: 过滤空白项
