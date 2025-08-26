# CCCC — 两个对等 AI 的协作编排器（内部备忘）
- 目的：让两位平等的 AI（PeerA/PeerB）在同一仓库、同一分支上，以“证据优先 + 小步快跑”的方式协作，尽量减少人工干预到关键决策点。
- 机制：Mailbox 文件作为唯一权威信道；tmux 仅作可视化。只有补丁/测试/日志（Evidence）改变系统状态。

## 核心能力
- 双 CLI 长连：tmux 四分屏（A/B + ledger + 状态面板）。
- Mailbox 协议：`.cccc/mailbox/peerX/{to_user.md,to_peer.md,patch.diff}` 收发结构化内容。
- 提交队列：补丁预检（`git apply --check`）→ 应用 → Lint/快测 → `git commit` → 记账。
- 规则收敛：默认“≤150 行改动/补丁”；超限通过 RFD 走例外（需双签与更严格门禁）。
- 触发器→输出：最小合规发声（ACK/EVIDENCE/CLAIM/QUESTION/COUNTER），减少无效闲聊。

## 为什么用 Mailbox
- 避免解析不同 CLI 的 TUI/回显差异；让 Evidence 可回放、可审计。
- 终端视图只做“感觉良好”的可视化；真实决策以 mailbox 与 ledger 为准。

## 快速开始
```bash
# 依赖：git, tmux, Python 3.10+
python3 -m venv .venv && source .venv/bin/activate
pip install pyyaml

# 运行（在本仓库）
python cccc.py
# 或迁移脚手架到你的仓库
python cccc.py init --to /path/to/repo  # --force 可覆盖
```

启动后：
- tmux 会话名：`cccc-<repo>`，四分屏：A（左上）/B（右上）/ledger（左下）/状态（右下）。
- 控制台输入：`a: 文本` / `b: 文本` / `u: 文本（或 both: 文本）`。
- 任何补丁会自动走预检→应用→lint/test→提交，并记录到 `.cccc/state/ledger.jsonl`。

## 配置（启动时读取）
- `.cccc/settings/cli_profiles.yaml`
  - `commands.peerA|peerB`: CLI 启动命令（未设则回退内置 Mock）。
  - `delivery_mode.peerA|peerB`: `tmux` | `bridge`（建议 B 在 tmux，TUI 更稳）。
  - `peer*.inbound_suffix`: tri‑suffix（from_user/from_peer/from_system），由你手动维护。
- `.cccc/settings/policies.yaml`
  - `patch_queue.max_diff_lines` 默认 150；`allowed_paths` 路径白名单。
  - `handoff_filter` 低信号抑制策略（冷却、关键词、去重等）。
- `.cccc/settings/roles.yaml`：当前 leader（用于状态显示）。

提示：现阶段不做运行时热更新；修改上述配置需重启 `python cccc.py` 生效。

## 运行时 SYSTEM（极简）
- 由 `prompt_weaver.py` 动态拼装（Mailbox 合约 + Inbound markers + Speak‑up Triggers）。
- 历史长文 prompt 已归档到 `docs/bak/`，不再参与运行时注入。

## Speak‑up Triggers（最小高信号输出）
- 收到含 `[MID]` 的消息：在 CLI 输出 `<SYSTEM_NOTES>ack: <MID></SYSTEM_NOTES>`。
- 有最小产物：发 EVIDENCE（小补丁/测试；否则 3–5 行稳定日志，含命令/行号）。
- 有下一步但无产物：发 CLAIM（1–3 任务；每个含 constraints + acceptance）。
- 被单点不确定阻塞：只问一个可判定的 QUESTION。
- 不同意：先 steelman，再发 COUNTER（附复现/度量）。

## 调试与可视化
- `CONSOLE_ECHO`：是否在 orchestrator 控制台回显 AI 输出（当前默认 true，便于调试）。后续可考虑迁移到配置文件（启动时读取即可，无需热更）。
- tmux 常用：切 pane（Ctrl‑b 方向键）；脱离（Ctrl‑b d）。滚动历史：滚轮上进入 copy‑mode，`q`/`Esc` 退出。

## 策略：为何保留 150 行上限
- 小步可回滚、评审成本低，证据循环更快；不限制创意，只鼓励拆分与可验证。
- 需要大改时：用 RFD 走例外（双签、严格门禁）或拆成补丁序列。

## 已知限制（现阶段）
- 无运行时热更新：修改配置需重启。
- bridge 适配器仍在演进（已做 DSR/SGR 修正）；TUI 较重的 CLI 建议用 tmux 模式。
- 未接入外部消息桥（如 Telegram）；仅本地 tmux 可视化。

## 路线（短期）
- M0→M1：稳定提交队列/软锁/证据账本；完善失败原因与观测。
- M1.5：可选的配置热加载（仅 policies/roles），并保留极简 SYSTEM。
- M2：外部桥接（Telegram）；RFD 内联；差异追踪与自修复提案。
