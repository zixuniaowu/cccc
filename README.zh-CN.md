# CCCC vNext（重写进行中）

CCCC 正在重写为一个**全局多智能体交付内核**。

## vNext 形态

- **全局运行时目录**：`~/.cccc/`（working group / scopes / ledger / runtime state）
- **核心单位**：Working Group（类似群聊）+ 复数 Scopes（目录 URL）
- **每个 group 一份追加式账本**：`~/.cccc/groups/<group_id>/ledger.jsonl`

## 快速开始（开发态）

```bash
pip install -e .
export CCCC_HOME=~/.cccc   # 可选（默认就是 ~/.cccc）
cccc attach .
cccc groups
cccc send <group_id> "hello"
cccc tail <group_id> -n 20
```

规划文档：`docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`

旧版 0.3.x 已以 tag `v0.3.28` 归档。
