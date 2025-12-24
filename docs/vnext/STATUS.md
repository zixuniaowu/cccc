# CCCC vNext — Current Status & Roadmap (Living Doc)

> 目标：把 CCCC 从“每仓库一份运行态 + tmux 编排”升级为 **全局唯一的交付协作中枢**：一个常驻 `ccccd` 管理多个 working group，Web/CLI/IM 都只是入口（port），一切关键事实落在 group 的 ledger。

## 0) 我们离“做完”还差多远？

当前实现处于 **Kernel MVP 已跑通，但远未完成产品化** 的阶段：

- 已跑通：working group / actor / PTY runner / Web 控制台 / ledger + targeted delivery / SYSTEM 注入 / nudge+self-check（简化版）。
- 仍缺：稳定的治理与交付闭环（RFD/decision/approval）、IM bridge、headless mode、可扩展的消息/事件规范、状态恢复与历史查询、权限与安全护栏、UI 体验打磨。

换句话说：现在解决的是“能跑 + 核心路径连通”，接下来要解决的是“可长期运行 + 可恢复 + 可扩展 + 低摩擦”。

## 1) vNext 形态（我们要达成的终局）

**一个全局实例（`CCCC_HOME`）**
- 默认 `~/.cccc/`（可用 `CCCC_HOME` 覆盖）
- 多个 working group：`CCCC_HOME/groups/<group_id>/`

**一个常驻内核（`ccccd`）**
- 单写者：负责写 ledger、管理 actors、调度自动化
- 崩溃后可恢复：从工作目录重建运行态（MVP：杀掉遗留 PTY 进程并按“running=true groups”自动重启）

**多个 ports（Web/CLI/IM/…）**
- port 不持有真相：都通过 daemon 读写事实流
- Web 负责：群聊式控制台 + 轻量介入（必要时提供 terminal）
- CLI 负责：脚本化、快速运维、故障排查
- IM 负责：远程通知/控制（后置）

## 2) 已实现（可运行的能力清单）

### 2.1 Daemon / Runtime
- 全局 daemon：`ccccd`（unix socket IPC）
- 全局 home：`CCCC_HOME`（groups/daemon 目录结构）
- PTY runner：daemon 管理每个 actor 的 PTY 会话（无 tmux）
- 崩溃清理：daemon 启动时杀掉 pidfile 指向的孤儿 PTY
- Desired run-state：`group.yaml: running=true/false`，daemon 启动可自动拉起 running groups

### 2.2 Working Group / Actor
- group：create / update(title/topic) / attach(scope) / start / stop / delete
- actor：add / update / start/stop/restart / remove
- 权限（简版）：peer 只能管理自己；foreman 可管理同组 peers

### 2.2.1 Agent Guidance（角色/规程注入策略）
- 设计取舍与原因：见 `docs/vnext/AGENT_GUIDANCE.md`
- 默认原则：共享 skills（不做 per-actor skills 隔离）+ 角色参数化 + 内核 RBAC + 启动时极短 bootstrap

### 2.3 Ledger（事实流）
- ledger.jsonl：统一 envelope（`v/id/ts/kind/group_id/scope_key/by/data`）
- 已知 kind 数据结构校验（contracts v1）
- **内容硬规则（已落地）**
  - ledger 不允许超大事件行（硬上限 `MAX_EVENT_BYTES`）
  - 大 chat 文本会落盘到 `state/ledger/blobs/`，ledger 只保留引用（避免把日志/长文塞进 ledger）

### 2.4 Delivery（消息投递）与 SYSTEM 注入
- `cccc send --to ...` 会写入 `chat.message` 事件
- 显式收件人（`to != []`）会 best-effort 注入到目标 actor 的 PTY（格式统一为 `[cccc] <by> → <to>: ...`）
- SYSTEM 注入：actor start/restart/group start/autostart 时注入 `render_system_prompt()`
- 注入策略：
  - PTY 具备 bracketed-paste 时，多行用 bracketed-paste
  - 否则多行落盘到 `state/delivery/<actor>.txt` 并注入文件指针（避免“半行粘贴导致误执行”）

### 2.5 Automation（简化版）
- NUDGE：actor inbox 有消息超时未读时注入提醒
- SELF-CHECK：每 N 次“被投递”触发一次自检提示；每 M 次自检刷新 SYSTEM

### 2.6 Web Port（本地）
- FastAPI 提供 REST + SSE ledger stream + WS terminal
- Web UI（React/Vite + xterm.js）可查看 group/actors、发送消息、打开 terminal
- Web token（当前实现）：若设置 `CCCC_WEB_TOKEN`，HTTP 用 `Authorization: Bearer <token>`；WS terminal 用 `?token=<token>`（未统一，后置）
- WS terminal（当前实现）：暂未做 writer 抢占/释放；默认假设同一时刻只有一个前端在“写入键盘输入”

## 3) 仍未完成（高优先级缺口）

### P0：稳定性与可恢复性（不做会卡死后续）
- 统一“消息投递协议”与 wrapper：目前只做了最小格式，缺“kind/priority/ack/inflight”语义
- inbox/已读/游标：当前是最小实现，缺更强的增量读取与历史查询（尤其是 ledger 变大后）
- 明确“系统通知 vs 聊天消息”分层（避免把系统噪音塞进用户对话）
- Headless loop（daemon 侧编排）未实现：自动推进/验收/停机的闭环还缺核心组件

### P1：产品化入口
- IM bridge（Telegram/Slack/…）按 group 绑定（对齐旧版语义）
- 统一 auth/token UX（后置，用户已同意先不做）
- 更强的 UI：群聊列表、搜索、消息分页、actor 运行态/日志等

### P2：治理与扩展（决定 cccc 是否“经得起考验”）
- RFD/decision/approval 机制（结构化 + 可追溯）
- MCP：对外暴露 control-plane（让 agents 能自我调度 cccc）
- 多 scope/多 repo 协作与证据引用（refs/attachments 的硬规范 + 可迁移引用）

## 4) Ledger Snapshot/Compaction（刚补齐的骨架）

目标：让 ledger **长期运行不膨胀**，同时不破坏“事实可追溯”。

- Snapshot：`state/ledger/snapshots/snapshot.<ts>.json`（同时写 `snapshot.latest.json`）
- Compaction（保守策略）：
  - 触发条件：active ledger 超过 `ledger.max_active_bytes`（默认 50MB）且距离上次 compact 超过 `ledger.min_interval_seconds`（默认 300s）
  - 安全水位：只归档“全体 actors 都已读”的事件（用 `state/read_cursors.json` 的全局最小 `ts`）
  - 保留尾部：始终保留 active ledger 尾部 `ledger.keep_tail_lines`（默认 2000 行），避免 UI 失去最近上下文
  - 归档位置：`state/ledger/archive/ledger.<ts>.jsonl`，并写入 `state/ledger/compaction.json`
  - daemon 会周期性尝试 compact（当前每 60s 检查一次，但会按阈值/间隔/游标自动跳过）

对应 CLI：
- `cccc ledger snapshot [--group <id>]`
- `cccc ledger compact [--group <id>] [--force]`

## 5) 下一步建议（我认为 ROI 最高的顺序）

1. **把“消息/事件规范”定硬**：定义 message record 的最小字段集 + ack/inflight + system-notify 分层（否则自动化会越做越乱）。
2. **补齐 headless loop 的最小闭环**：至少能做到“收消息 → 执行 → 汇报 → 等决策 → 继续/停机”。
3. **IM bridge 回归（按 group）**：这是远程介入/通知的最短路径。
