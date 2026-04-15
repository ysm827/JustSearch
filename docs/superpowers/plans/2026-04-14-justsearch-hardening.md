# JustSearch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 JustSearch 补齐安全边界、修复失效的 FTS 搜索、增强前端稳定性，并修正浏览器上下文轮换目录问题。

**Architecture:** 新增独立鉴权模块，统一处理 token 生成、HTTP 访问控制和 WebSocket 校验；数据库侧通过 FTS 同步函数与初始化回填恢复全文搜索；前端新增轻量 auth helper 统一 token 读取与请求头注入；浏览器上下文目录按固定槽位映射，避免轮换后串目录。

**Tech Stack:** FastAPI, Starlette middleware, SQLite FTS5, 原生 ES Modules, Node built-in test runner, pytest

---

### Task 1: Add Regression Tests For Security And FTS

**Files:**
- Create: `tests/test_auth_and_db.py`
- Create: `tests/frontend/auth.test.mjs`

- [ ] 写失败测试，覆盖 Bearer 鉴权、loopback 例外、FTS 写入同步、前端 token 解析与 WS URL 构造
- [ ] 运行测试确认它们先失败

### Task 2: Implement Backend Access Control

**Files:**
- Create: `backend/app/auth.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/chat.py`

- [ ] 实现 token 生成/加载、HTTP middleware、WebSocket 校验与 HTML bootstrap 注入
- [ ] 将主应用接到新鉴权逻辑，并把 chat 限流改为按客户端区分

### Task 3: Restore Search Indexing

**Files:**
- Modify: `backend/app/database.py`
- Modify: `backend/app/routers/history.py`

- [ ] 为 FTS 建立回填与后续写入同步逻辑
- [ ] 保持全文搜索接口继续使用现有查询路径

### Task 4: Fix Frontend Bootstrap/Auth Flow

**Files:**
- Create: `backend/static/js/modules/auth.js`
- Modify: `backend/static/js/modules/api.js`
- Modify: `backend/static/js/main.js`

- [ ] 统一处理 bootstrap token、URL token 与请求头注入
- [ ] 修复 `history` 变量遮蔽和设置加载失败兜底
- [ ] 给浏览器控制 WebSocket 带上 token

### Task 5: Stabilize Browser Context Slot Directories

**Files:**
- Modify: `backend/app/browser_context.py`

- [ ] 将 context 的 `user_data_dir` 固定绑定到槽位编号
- [ ] 保持初始化、轮换与补位逻辑一致

### Task 6: Update Docs And Verify

**Files:**
- Modify: `README.md`

- [ ] 更新 README 中关于认证和远程访问的说明
- [ ] 运行 pytest、node tests 与 compile 验证

