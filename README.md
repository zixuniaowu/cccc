# CCCC
Claude Code x Codex CLI Pair Programming Flow
两个对等 AI 的结伴编程编排器。单分支、对话可视化、证据优先。

## 亮点
- 对等辩论：S‑Pair 协议（CLAIM/COUNTER/EVIDENCE + 轮转队长/挑战者）。
- 群聊可视化：`<TO_USER>` 给人看，`<TO_PEER>` 给同伴看（可折叠）。
- 单分支快跑：提交队列 + 软锁；小步补丁 ≤ 150 行。
- 事实优先：只有 EVIDENCE（补丁/测试/日志）改变系统状态；聊天只是可视化。

## 快速开始（本地）
```bash
# 1) 准备环境（需要 git 与 tmux 可执行）
python3 -m venv .venv && source .venv/bin/activate
pip install pyyaml

# 2) 选择运行方式（二选一）
# 2A. 真正的 CLI（如已安装）
export CLAUDE_I_CMD='claude-code chat'
export CODEX_I_CMD='codex chat'

# 2B. 无外部 CLI：使用内置 Mock Agent 进行冒烟测试
#    仅用于验证预检→提交→记账流水是否正常
export CLAUDE_I_CMD='python .cccc/mock_agent.py --role peerA'
export CODEX_I_CMD='python .cccc/mock_agent.py --role peerB'

# 3) （可选）预检命令：lint / 快测
export LINT_CMD='ruff check || echo "no lint"'
export TEST_CMD='pytest -q || echo "no tests"'

# 4) 运行 orchestrator（tmux 双 pane）
python cccc.py
```

运行后：
- 将创建/复用一个 tmux 会话 `cccc-<repo>`，左 pane 为 PeerA（Claude），右 pane 为 PeerB（Codex）。
- 你会被提示输入一行“模糊愿景”；随后 A/B 按协议往返几轮，并在有补丁时执行预检：
  - `git apply --check` 预检 → `git apply` → `LINT_CMD` → `TEST_CMD`。
  - 变更达标即 `git commit`，并写入账本。

若选择 2B（Mock Agent），首次轮次会自动向 README.md 追加一行，便于验证预检流水。

## 配置与账本
- 策略配置：`.cccc/settings/policies.yaml`
  - `patch_queue.max_diff_lines`（默认 150）
  - `patch_queue.allowed_paths`（路径白名单）
- 角色与特质：`.cccc/settings/roles.yaml`、`.cccc/settings/traits.yaml`
- 交付/队列：`.cccc/settings/cli_profiles.yaml`
- 事件账本：`.cccc/state/ledger.jsonl`

提示：顶层 README 早期版本提到 `orchestrator/orchestrator_poc.py`，现已统一入口为 `python cccc.py`，由其加载 `.cccc/orchestrator_tmux.py`。

## 没有 tmux 时如何安装
- Ubuntu/Debian: `sudo apt update && sudo apt install -y tmux`
- Fedora: `sudo dnf install -y tmux`
- Arch: `sudo pacman -S --noconfirm tmux`
- macOS (Homebrew): `brew install tmux`
- Windows: 建议使用 WSL + 以上命令，或使用终端复用器替代（需自行改造 orchestrator）。
