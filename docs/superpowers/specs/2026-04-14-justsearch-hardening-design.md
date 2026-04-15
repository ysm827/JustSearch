# JustSearch Hardening Design

## Goal

修复当前项目里最影响真实使用的几类问题：

- API 与浏览器控制缺乏有效鉴权
- 历史全文搜索索引未同步
- 前端启动与路由存在明显稳健性问题
- 浏览器上下文轮换时 profile 目录不稳定

## Chosen Approach

采用兼顾兼容性与安全性的方案：

- 对 `/api` 和 `/ws/browser/*` 增加 Bearer 鉴权
- 默认对 loopback 客户端放行，保留本机单页应用的无感体验
- 为本机页面自动注入 token；远程访问支持通过 `?token=` 引导前端持有 token
- 将默认 CORS 收紧到本地开发来源
- 为 SQLite FTS 增加初始化回填与后续写入同步
- 修复前端 `history` 变量遮蔽和设置加载空值兜底
- 将浏览器上下文使用的 `user_data_dir` 改为显式按槽位编号分配

## Non-Goals

- 不重做 UI
- 不引入新的前端构建链路
- 不改变核心搜索工作流

