# Changelog

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
