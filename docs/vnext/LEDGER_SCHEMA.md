# CCCC vNext — Ledger Schema (v1)

本项目的**唯一事实源**是每个 working group 的追加式账本：

`CCCC_HOME/groups/<group_id>/ledger.jsonl`

文件格式是 **JSON Lines**：每行一个 JSON object（append-only）。任何 UI/IM/CLI 都只应通过 daemon 写入（MVP 允许 CLI fallback，本质仍应收敛到单写者）。

## 1) Event Envelope（统一外壳）

每一行都是一个事件（Event），外壳字段固定：

```jsonc
{
  "v": 1,
  "id": "event-id",
  "ts": "2025-01-01T00:00:00.000000Z",
  "kind": "chat.message",
  "group_id": "g_xxx",
  "scope_key": "s_xxx",
  "by": "user",
  "data": {}
}
```

- `v`: envelope 版本（当前固定 1）
- `id`: 事件 id（uuid4 hex）
- `ts`: UTC ISO8601（Z）
- `kind`: 事件类型（见下文）
- `group_id`: 归属 group
- `scope_key`: 事件关联的 scope（可为空字符串表示 group 级）
- `by`: 发起者（`user` 或 actor id；系统内部也可用 `system`/`cli`/`web`）
- `data`: kind 对应的数据体（v1 对已知 kind 进行校验；未知 kind 仍允许写入 dict）

## 2) v1 Known Kinds & Data Shapes

> 说明：这里定义的是 **v1 的最小可验收集合**。后续新增 kind 时，不应破坏 envelope。

### Group

- `group.create`
  - `data`: `{ "title": str, "topic": str }`
- `group.update`
  - `data`: `{ "patch": { "title"?: str, "topic"?: str } }`（至少包含一个字段）
- `group.attach`
  - `data`: `{ "url": str, "label": str, "git_remote": str }`
  - 约定：事件外壳的 `scope_key` 填“被 attach 的 scope_key”（便于索引/过滤）
- `group.detach_scope`
  - `data`: `{ "scope_key": str }`
- 约定：事件外壳的 `scope_key` 同样填该 `scope_key`
- `group.set_active_scope`
  - `data`: `{ "path": str }`（用户选择的路径；daemon 会据此解析 scope，并把事件外壳的 `scope_key` 写成“新的 active scope”）
- `group.start`
  - `data`: `{ "started": list[str] }`（启动的 actor ids）
- `group.stop`
  - `data`: `{}`（保留为空）

### Actor

- `actor.add`
  - `data`: `{ "actor": Actor }`
- `actor.update`
  - `data`: `{ "actor_id": str, "patch": { ... } }`
  - `patch` 允许字段：`role/title/command/env/default_scope_key/submit/enabled`（不允许其它 key）
- `actor.set_role`
  - `data`: `{ "actor_id": str, "role": "foreman" | "peer" }`
- `actor.start` / `actor.stop` / `actor.restart` / `actor.remove`
  - `data`: `{ "actor_id": str }`

### Chat

- `chat.message`
  - `data`: `ChatMessageData`
    - `text: str`
    - `format: "plain" | "markdown"`
    - `to: list[str]`（空 = broadcast；非空 = 显式收件人）
    - `reply_to: str | null`（回复哪条消息的 event_id）
    - `quote_text: str | null`（被引用消息的文本片段，便于展示）
    - `thread: str`（预留：话题/线程 ID）
    - `refs: list[object]`（引用：文件/commit/URL）
    - `attachments: list[object]`（附件元信息）
    - `client_id: str | null`（客户端去重 ID，幂等）
- `chat.read`
  - `data`: `{ "actor_id": str, "event_id": str }`
  - 语义：actor 标记已读到指定消息（含之前所有消息）
- `chat.reaction`（后置）
  - `data`: `{ "event_id": str, "actor_id": str, "emoji": str }`
  - 语义：对某条消息的快速反馈（✅/❌/👍/🤔）

## 3) Routing Semantics（`to` 语义约定）

`chat.message.data.to` 是 **收件人 token 列表**：

- 空数组：broadcast（所有 actor 都“可读”，但不一定会“被投递进 PTY”）
- 非空：显式路由（daemon 可对 running actors 做 best-effort 投递）

v1 内置 token（约定）：

- `user` / `@user`：用户本人
- `@all`：所有 actors
- `@peers`：role=peer 的所有 actors
- `@foreman`：role=foreman 的 actor
- actor id：例如 `peer-a`（输入时也允许写成 `@peer-a`，会被规范化为 `peer-a`）
- actor title：允许用 title 作为收件人输入（大小写不敏感）；若 title 不唯一会报错；写入 ledger 时会被解析为 actor id（ledger 不存 title）

> 说明：收件人 tokens 会在写入前进行规范化与去重（例如把 `@user` 变成 `user`、把 title 解析成 id、剔除重复 token），以保证 ledger 长期可维护与可索引。

## 4) Size & Evidence（保持 ledger 可持续）

ledger 只记“事实与引用”，不应塞入大段日志或大文件内容。

- 大内容用 refs/attachments 引用（路径/URL/hash/大小等）
- 实现约束（已落地）：
  - 事件行有硬大小上限（避免 ledger 被超大 JSON 撑爆）
  - 大 chat 文本会落盘到 `groups/<group_id>/state/ledger/blobs/chat.<event_id>.txt`，ledger 只保留引用
- 允许 UI 侧做“展示缓存”，但不能引入第二真相源

## 5) Snapshot / Compaction（骨架）

- Snapshot：`groups/<group_id>/state/ledger/snapshots/snapshot.<ts>.json`（同时写 `snapshot.latest.json`）
- Archive：`groups/<group_id>/state/ledger/archive/ledger.<ts>.jsonl`
