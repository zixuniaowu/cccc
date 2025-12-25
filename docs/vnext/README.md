# CCCC vNext Docs (Start Here)

本目录用于固化 vNext 的**形态/边界/契约**与**当前实现状态**，确保重启会话后能快速恢复上下文。

推荐阅读顺序：

1) `docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`：总体形态与架构边界（全局 daemon、working groups、scopes、ports）
2) `docs/vnext/STATUS.md`：当前实现进度与缺口（Living Doc）
3) `docs/vnext/LEDGER_SCHEMA.md`：事实流（ledger）v1 的最小字段与事件类型约定
4) `docs/vnext/AGENT_GUIDANCE.md`：在拿不到 system prompt 常驻权限时，如何让 agents 稳定理解角色/边界/规程（含 skills 共享目录的取舍）
5) `docs/vnext/IM_MESSAGING.md`：IM 风格消息机制设计（回复、已读、投递）

约定：
- **单一事实源**：`CCCC_HOME/groups/<group_id>/ledger.jsonl` 是 group 的共享事实流（append-only）。
- **文档不重复**：长规程/Playbook 只维护一处（参见 `AGENT_GUIDANCE.md` 的“不要三套文案各写一遍”）。

