# CCCC vNext — IM Bridge 设计

> 让用户通过熟悉的 IM 界面与 CCCC 工作组交互

## 1. 设计原则

### 1.1 核心约束

1. **1 Group = 1 Bot**: 每个 Group 最多绑定一个 IM Bot，简单、隔离、易理解
2. **显式订阅**: Chat 必须 `/subscribe` 后才能接收消息，确保安全和用户可控
3. **Ports 是薄层**: IM Bridge 只做消息转发，不持有业务逻辑，daemon 是唯一状态源
4. **独立进程**: 每个 Group 的 IM Bridge 作为独立进程运行，互不干扰

### 1.2 不支持的场景

- 多个 Group 共用一个 Bot（会导致消息混乱）
- 一个 Group 同时绑定多个 IM 平台（行为未定义）

## 2. 架构

```
┌─────────────────────────────────────────────────────────┐
│                     Group A                              │
│  ┌─────────────┐     ┌─────────────┐                    │
│  │ IM Bridge   │────▶│   Daemon    │                    │
│  │ (Telegram)  │◀────│  (ccccd)    │                    │
│  └──────┬──────┘     └──────┬──────┘                    │
│         │                   │                            │
│         ▼                   ▼                            │
│  ┌─────────────┐     ┌─────────────┐                    │
│  │ Telegram    │     │  Ledger     │                    │
│  │ Bot API     │     │  .jsonl     │                    │
│  └─────────────┘     └─────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

### 2.1 数据流

**Inbound (IM → CCCC)**:
```
IM Message → Bridge → call_daemon(op="send") → Ledger → Delivery → PTY
```

**Outbound (CCCC → IM)**:
```
Ledger Event → Bridge (tail) → Filter (subscribed chats) → IM API → Chat
```

## 3. 配置

### 3.1 Group 级别配置

存储在 `CCCC_HOME/groups/<group_id>/group.yaml`:

```yaml
group_id: g_xxx
title: "My Project"
# ... 其他字段 ...

im:
  platform: telegram          # telegram | slack | discord
  token_env: TELEGRAM_BOT_TOKEN  # 从环境变量读取 token
  # token: "123456:ABC..."    # 或直接指定（不推荐，仅测试用）
```

### 3.2 运行时状态

存储在 `CCCC_HOME/groups/<group_id>/state/im_subscribers.json`:

```json
{
  "123456789": {
    "subscribed": true,
    "verbose": true,
    "subscribed_at": "2025-01-15T10:30:00Z",
    "chat_title": "My Dev Group"
  },
  "-987654321": {
    "subscribed": true,
    "verbose": false,
    "subscribed_at": "2025-01-15T11:00:00Z",
    "chat_title": "Private Chat"
  }
}
```

### 3.3 Bridge 进程状态

```
CCCC_HOME/groups/<group_id>/state/
├── im_bridge.pid           # Bridge 进程 PID
├── im_bridge.log           # Bridge 日志
└── im_subscribers.json     # 订阅状态
```

## 4. IM 命令

### 4.1 消息发送

| 命令 | 说明 | 示例 |
|------|------|------|
| 直接发消息 | 发给所有 agents | `请帮我实现登录功能` |
| `@<actor> 消息` | 发给指定 actor | `@peer-a 请 review 这个 PR` |
| `@all 消息` | 强制投递给所有 | `@all 停下来，我们需要讨论` |
| `@foreman 消息` | 发给 foreman | `@foreman 当前进度如何？` |

### 4.2 订阅控制

| 命令 | 说明 |
|------|------|
| `/subscribe` | 订阅当前 chat，开始接收消息 |
| `/unsubscribe` | 取消订阅，停止接收消息 |

### 4.3 显示控制

| 命令 | 说明 |
|------|------|
| `/verbose` | 切换详细模式（toggle）：on=显示所有消息，off=只显示发给用户的消息 |

**默认值**: `verbose=true`（初期让用户看清 agent 行为）

### 4.4 状态查询

| 命令 | 说明 |
|------|------|
| `/status` | 显示 Group 状态（running/state）和 agents 状态 |
| `/context` | 显示 Group context（vision/sketch/milestones/tasks） |

### 4.5 控制命令

| 命令 | 说明 |
|------|------|
| `/pause` | 暂停消息投递（设置 group state=paused） |
| `/resume` | 恢复消息投递（设置 group state=active） |
| `/launch` | 启动所有 agents |
| `/quit` | 停止所有 agents |

### 4.6 帮助

| 命令 | 说明 |
|------|------|
| `/help` | 显示命令帮助 |

## 5. Outbound 消息过滤

### 5.1 过滤逻辑

```python
def should_forward_to_im(event: LedgerEvent, verbose: bool) -> bool:
    kind = event.get("kind", "")
    
    # 系统通知总是转发
    if kind == "system.notify":
        return True
    
    # 聊天消息
    if kind == "chat.message":
        to = event.get("data", {}).get("to", [])
        by = event.get("by", "")
        
        # verbose=on: 转发所有消息
        if verbose:
            return True
        
        # verbose=off: 只转发 to:user 的消息
        if "user" in to or not to:  # 空 to 视为广播
            return True
        
        return False
    
    return False
```

### 5.2 消息格式

**Agent → User**:
```
[peer-a] 已完成登录功能的实现，PR 链接：https://...
```

**Agent → Agent** (verbose=on):
```
[peer-a → peer-b] 请帮我 review 这个 PR
```

**System Notify**:
```
[SYSTEM] Group state changed: active → paused
```

## 6. CLI 命令

### 6.1 配置管理

```bash
# 设置 IM 配置（交互式）
cccc im set telegram [--group <group_id>]
# 提示: Enter token or token_env name: TELEGRAM_BOT_TOKEN

# 设置 IM 配置（非交互式）
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN [--group <group_id>]

# 查看当前配置
cccc im config [--group <group_id>]

# 清除 IM 配置
cccc im unset [--group <group_id>]
```

### 6.2 Bridge 管理

```bash
# 启动 IM Bridge
cccc im start [--group <group_id>]

# 停止 IM Bridge
cccc im stop [--group <group_id>]

# 查看状态
cccc im status [--group <group_id>]

# 查看日志
cccc im logs [--group <group_id>] [-f] [-n 50]
```

## 7. Web UI 设置

### 7.1 Settings Modal 改造

Settings Modal 改为 Tab 形式，添加 "IM Bridge" Tab：

```
┌─────────────────────────────────────────┐
│ ⚙️ Settings                         ✕  │
├─────────────────────────────────────────┤
│ [Timing] [IM Bridge]                    │
├─────────────────────────────────────────┤
│                                         │
│  Platform:  [Telegram ▼]                │
│                                         │
│  Token:     ○ Environment Variable      │
│             [TELEGRAM_BOT_TOKEN    ]    │
│                                         │
│             ○ Direct Token (not rec.)   │
│             [••••••••••••••••••••• ]    │
│                                         │
│  Status:    ● Running                   │
│  Subscribers: 2 chats                   │
│                                         │
│  [Start Bridge]  [Stop Bridge]          │
│                                         │
└─────────────────────────────────────────┘
```

### 7.2 UI 组件

- Platform 下拉框：Telegram / Slack / Discord
- Token 输入：支持环境变量名或直接输入（不推荐）
- 状态显示：Running / Stopped
- 订阅者数量显示
- Start/Stop 按钮

## 8. 实现结构

```
src/cccc/ports/im/
├── __init__.py
├── bridge.py              # Bridge 核心逻辑
│   ├── IMBridge           # 抽象基类
│   ├── start_bridge()     # 启动入口
│   └── LedgerWatcher      # Ledger 监听器
├── commands.py            # IM 命令解析器
├── adapters/
│   ├── __init__.py
│   ├── base.py            # Adapter 抽象基类
│   ├── telegram.py        # Telegram Bot API
│   ├── slack.py           # Slack Socket Mode
│   └── discord.py         # Discord Gateway
└── cli.py                 # CLI 命令实现
```

## 9. 代码复用（从 v0.3.28）

### 9.1 复用清单

从 `old_v0.3.28/.cccc/adapters/` 复用以下代码：

| 源文件 | 目标文件 | 复用内容 |
|--------|----------|----------|
| `bridge_telegram.py` | `adapters/telegram.py` | API 调用、long-poll、消息格式化、速率限制 |
| `bridge_slack.py` | `adapters/slack.py` | Socket Mode 连接、API 调用 |
| `bridge_discord.py` | `adapters/discord.py` | Gateway 连接 |
| `outbox_consumer.py` | `ledger_watcher.py` | cursor-based tail、断点续传 |

### 9.2 Telegram 复用细节

从 `bridge_telegram.py` 复用：

- `tg_api()` - API 调用封装（JSON 编码、超时、错误处理）
- `tg_poll()` - Long-poll getUpdates
- `_summarize()` - 消息摘要（保留换行、限制行数/字符）
- `_compose_safe()` - 安全消息组装（Telegram 4096 字符限制）
- `_send_with_one_retry()` - 带重试的发送
- `_acquire_singleton_lock()` - 单例锁防止重复实例
- `load_subs()` / `save_subs()` - 订阅者管理
- `RateLimiter` 类 - 速率限制
- `_save_file_from_telegram()` - 文件下载

### 9.3 Ledger Watcher 复用

从 `outbox_consumer.py` 改造为 `LedgerWatcher`：

- 复用 cursor-based tail 逻辑（exactly-once 语义）
- 复用文件轮转检测（inode 变化）
- 复用断点续传机制
- 改造：监听 `ledger.jsonl` 而非 `outbox.jsonl`
- 改造：过滤 `kind="chat.message"` 事件

### 9.4 不复用的部分

| 原代码 | 原因 |
|--------|------|
| `_route_from_text()` | 老版本 peerA/peerB 硬编码，vNext 用动态 actor_id |
| `_deliver_inbound()` | 老版本写 mailbox 文件，vNext 调用 daemon API |
| 配置读取逻辑 | 老版本从 `.cccc/settings/`，vNext 从 `group.yaml` |

## 10. 安全考虑

### 10.1 Token 管理

- **推荐**: 使用环境变量 (`token_env`)
- **不推荐**: 直接在配置中写 token（仅测试用）
- Token 不会写入 ledger 或日志

### 10.2 订阅机制

- 必须显式 `/subscribe` 才能接收消息
- 未订阅的 chat 可以发送消息，但不会收到回复
- 订阅状态存储在 group state 中，不在 ledger 中

### 10.3 权限

- 任何能访问 Bot 的用户都可以发送消息
- 如需限制，可在 IM 平台侧配置（如 Telegram 群组权限）

## 11. 实现优先级

### Phase 1: 核心框架
1. `src/cccc/ports/im/bridge.py` - Bridge 核心
2. `src/cccc/ports/im/commands.py` - 命令解析
3. CLI: `cccc im set/start/stop/status`

### Phase 2: Telegram Adapter
1. `src/cccc/ports/im/adapters/telegram.py`
2. Long-poll getUpdates
3. 消息发送

### Phase 3: Slack/Discord
1. 按需添加其他平台支持

## 12. 与老版本的差异

| 方面 | 老版本 (v0.3.x) | vNext |
|------|-----------------|-------|
| 配置位置 | `.cccc/settings/telegram.yaml` | `group.yaml` 的 `im` 字段 |
| 状态存储 | 分散在多个文件 | 集中在 `state/im_subscribers.json` |
| 消息源 | `outbox.jsonl` | `ledger.jsonl` |
| 投递目标 | 写 mailbox 文件 | 调用 daemon API |
| 路由 | peerA/peerB 硬编码 | 动态 actor_id |
| 多 Group | 不支持 | 原生支持 |
| verbose 默认 | off | on |

## 13. 未来扩展

- **Webhook 模式**: 对于 Slack/Discord，支持 webhook 替代 long-poll
- **消息格式化**: 支持 Markdown、代码块等富文本
- **文件传输**: 支持通过 IM 发送/接收文件
- **Reaction**: 支持 emoji 反应（如 ✅ 表示确认）
