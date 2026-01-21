# CCCC vNext — Status & Roadmap

> 最后更新：2025-12-28

## 当前状态：Backend 完成，Frontend 基本完成

### ✅ Backend Complete

- Daemon 架构（单写者原则）
- PTY + Headless runner
- MCP Server（38 tools, 4 namespaces）
- IM 风格消息（reply, quote, read receipts）
- 系统通知层（chat.message vs system.notify）
- Context 同步（vision/sketch/milestones/tasks/notes/refs/presence）
- SYSTEM prompt 注入
- 消息投递（PTY 直接注入 + Headless 通知）
- 消息限流（60s batch window）
- Ledger snapshot/compaction
- Multi-runtime 支持（claude, codex, droid, opencode）
- Foreman 自主能力（通过 MCP 管理 peers）
- 消息搜索/分页 API

### ✅ Frontend Complete

- Group 管理（创建/编辑/删除）
- Actor 管理（添加/启动/停止/编辑/删除）
- Agent-as-Tab UI 模式
- 终端嵌入（xterm.js）
- 消息发送/回复
- @mention 自动补全
- Context 面板
- Settings 面板
- IM Bridge 配置 UI
- 远程访问配置说明（Cloudflare Tunnel / Tailscale）
- 主题系统（Light/Dark/System）
- 移动端适配

### ✅ IM Bridge

- 核心框架完成
- Telegram adapter 完成
- Slack adapter 完成（Socket Mode + Web API）
- Discord adapter 完成（Gateway）
- Feishu adapter 完成（WebSocket + REST）
- DingTalk adapter 完成（Stream mode + REST）
- CLI 命令完成
- Web UI 配置完成

## 待完成 (P1.5)

| 项目 | 说明 |
|------|------|
| 消息搜索 UI | Backend API 已完成，前端集成待做 |
| 虚拟滚动 | 大消息列表性能优化 |

## 待完成 (P2)

| 项目 | 说明 |
|------|------|
| PyPI 发布 | `pip install cccc-pair` |
| RFD/决策机制 | 审批流程 |
| 多 scope 协作 | 跨仓库协作 |
| 跨 group 消息投递 | 跨 group 路由、权限与隔离 |
| 跨 group 附件/文件 | 复制 blobs 到目标 group 并重写引用（不默认落 repo） |
| 远程访问安全护栏 | `cccc doctor`/Web UI 检测风险暴露（未加鉴权时提示/阻止） |
| 远程访问分享 | `CCCC_PUBLIC_URL` + 分享链接/二维码（面向 Cloudflare Tunnel） |

## 待完成 (P3 / memo)

| 项目 | 说明 |
|------|------|
| Tunnel 可观测 | `cccc tunnel status/logs`（薄封装 cloudflared，状态落 CCCC_HOME） |
| 手机端 App | Capacitor 将 Web UI 打包为 iOS/Android（先做壳 + 深链/扫码/分享，推送后置） |

## 技术栈

| 层 | 技术 |
|----|------|
| Kernel/Daemon | Python + Pydantic |
| Web Port | FastAPI + Uvicorn |
| Web UI | React + TypeScript + Vite + Tailwind + xterm.js |
| MCP | stdio mode, JSON-RPC |

## 快速开始

```bash
# 安装
pip install -e .

# 启动
cccc

# 打开 Web UI
open http://127.0.0.1:8848/ui/
```

## CLI 命令速查

```bash
# Daemon
cccc                          # 启动 daemon + web
cccc daemon status            # 查看状态

# Group
cccc attach .                 # 创建/绑定工作组
cccc group create --title "My Project"
cccc group start              # 启动所有 actors
cccc group stop               # 停止所有 actors

# Actor
cccc actor add agent-1 --runtime claude
cccc actor start agent-1
cccc actor stop agent-1

# 消息
cccc send "Hello" --to @all
cccc reply <event_id> "Reply"
cccc tail -n 50 -f

# IM Bridge
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN
cccc im start
cccc im status

# 其他
cccc doctor                   # 环境检查
cccc runtime list             # 列出可用 runtime
cccc status                   # 总览
```

---

详细架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)
功能详解见 [FEATURES.md](./FEATURES.md)
历史文档见 [archive/](./archive/)
