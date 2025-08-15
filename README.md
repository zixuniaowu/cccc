# CCCC
Claude Code x Codex CLI Pair Programming Flow
两个对等 AI 的结伴编程编排器。单分支、对话可视化、证据优先。

## 亮点
- **对等辩论**：S‑Pair 协议（CLAIM/COUNTER/EVIDENCE + 轮转队长/挑战者）。
- **群聊可视化**：<TO_USER> 给人看，<TO_PEER> 给同伴看（可折叠）。
- **单分支快跑**：提交队列 + 软锁；小步补丁 ≤ 150 行。
- **事实优先**：只有 EVIDENCE（补丁/测试/日志）改变系统状态；聊天只是可视化。

## 快速开始（本地）
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install watchdog
export CLAUDE_CMD='claude-code chat --system -'
export CODEX_CMD='codex chat --system -'
export TEST_CMD='pytest -q || echo "no tests"'
python orchestrator/orchestrator_poc.py
