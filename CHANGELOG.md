# Changelog

All notable changes to JustSearch will be documented in this file.

## [2.0.0] - 2026-03-29

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
- 🔍 搜索引擎健康监控（EngineHealthMonitor）
- 📄 OpenGraph 元数据提取
- 🚫 PDF URL 检测和跳过
- 🧪 基础测试框架（pytest）
- 🏷️ 版本号管理（version.py v2.0.0）
- 🎯 搜索建议芯片（Hero 区域）
- ♿ 键盘可访问性（focus-visible）
- 🔧 环境变量配置（.env.example）
- 📋 CHANGELOG 文件

### Changed
- CORS 默认改为通配符，方便反向代理部署
- Docker 数据目录迁移到 `data/`（SQLite）
- 中文网站内容提取增强（微信/知乎/V2EX 等）
- 错误消息中文化和友好提示
- Chrome 启动参数优化（--disable-gpu 等）
- API 重试渐进式延迟（2s → 5s）
- SSE meta 事件包含模型名称
- 浏览器连接池动态大小
- 代码块限高 400px
- 搜索日志去重
- Relevance prompt 增加语言/时间偏好

### Fixed
- 修复 `workflow.py` 重复 import asyncio
- 修复 `settings_manager.py` broken import (SETTINGS_FILE)
- 清理 CSS 孤立 `}` 和重复规则
- 对话保存失败仍返回答案给用户
- 智能截断支持中文分号/问号
- 设置值范围校验

### Removed
- 清理 .patch 文件残留
- 移除 docker-compose 中废弃的 settings.json 和 chats/ 挂载
