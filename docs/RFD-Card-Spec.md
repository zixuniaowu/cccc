# RFD 卡片最小规范 v0.1

目标：在不改变“证据优先、聊天不改状态”的前提下，把高影响决策（架构/发布/越权变更）的请求以 RFD 卡片形式展示到 IM（Telegram），并记录审批结果。

最小字段（出现在 ledger 事件 `kind: RFD` 中）
- id: 短 ID（字符串，唯一；若缺省桥接以事件内容 hash 截断生成）
- title: 一行标题（简洁说明）
- summary: 2–4 行摘要（可选；与 title 至少存在其一）
- alternatives: 备选（可选，数组或一行简述）
- impact: 影响面/风险（可选）
- rollback: 回滚策略（可选）
- default: 超时默认（可选，如 "reject"|"approve"|"defer"）
- timeout_sec: 超时秒数（可选；未指定则不提示超时）
- from: 触发方（system|PeerA|PeerB|user）

桥接展示
- Telegram 发送一条消息 `[RFD] {title|summary}`，附 inline 按钮：Approve / Reject / Ask More。
- 点按按钮：桥接写入 ledger 事件 `kind: decision`，附 `rfd_id, decision, chat`。
- 扩展：后续可根据 `alternatives/impact/rollback/default/timeout_sec` 丰富展示与提示。

注意事项
- RFD 事件本身不会改变状态（不合入代码），只作为治理与审批的显化；实际变更仍由 patch/tests/checks 通过 gate。
- 建议由 Peer 在 to_peer.md 或系统流程产生日志化的 RFD 事件（以便可回放）。

