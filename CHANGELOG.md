# Changelog

All notable changes to JustSearch will be documented in this file.

## [Unreleased]

### Added
- 🔍 搜狗搜索引擎支持
- 📥 对话导出为 Markdown 文件
- 🌓 跟随系统主题模式（auto）
- ⏱️ 消息时间戳（hover 显示）
- 📊 Token 使用统计显示
- 🛡️ API 速率限制（30 次/分钟）
- 📱 PWA manifest 支持移动端添加到主屏
- 🎨 自定义 SVG favicon
- ⚡ LLM 调用指数退避重试（429/500/502/503）
- 🔒 页面内容缓存避免重复爬取
- ⏰ 搜索超时保护（60 秒）
- 🧹 数据库自动清理 90 天以上旧会话

### Changed
- CORS 默认改为通配符，方便反向代理部署
- Docker 数据目录迁移到 `data/`（SQLite）
- 中文网站内容提取增强（微信/知乎/V2EX 等）
- 错误消息中文化和友好提示
- Chrome 启动参数优化（--disable-gpu 等）
- API 重试渐进式延迟（2s → 5s）
- SSE meta 事件包含模型名称

### Fixed
- 修复 `workflow.py` 重复 import asyncio
- 修复 `settings_manager.py` broken import (SETTINGS_FILE)
- 清理 CSS 孤立 `}` 和重复规则
- 对话保存失败仍返回答案给用户
