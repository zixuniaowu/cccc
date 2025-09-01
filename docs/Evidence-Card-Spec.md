# 通用证据卡片（Evidence Card）最小规范 v0.1

目标：以通用、与领域无关的方式，把“可验证的工作成果”表达为工件（Artifact）与检查（Check），用于驱动编排器的证据优先闭环。聊天不改状态，证据才改状态。

——

一、术语
- Artifact：任意可引用的工件（文件/链接/片段）。
- Check：可运行的验证步骤（命令/脚本/规则），以退出码与日志片段给出结论。
- Evidence Card：一次提交/评审的证据描述，挂载 artifacts 与 checks，产出 verdict/metrics 与日志引用。

二、最小字段（YAML/JSON）
```yaml
kind: generic  # 自由字符串，便于分类/搜索（如 doc.style / research.citations / edu.quiz）
title: "一行标题（简述目标与范围）"
artifacts:
  - path: docs/draft.md          # 文件路径或 URL（优先文件路径）
    sha256: <可选，若为文件可填>   # 用于可重复性与晋升
    bytes:  <可选>
    note: "草稿V3，目标读者：入门"
checks:
  - name: readability
    run: "python3 tools/readability.py docs/draft.md"  # 任何可执行命令
    timeout_sec: 20
    allow_failure: false         # 允许失败但继续汇报
  - name: duplicate-ratio
    run: "python3 tools/dupcheck.py docs/draft.md --max 0.25"
    timeout_sec: 20
    allow_failure: false
meta:
  audience: beginner
  style: tutorial
  owner: peerA
```

三、运行与产出（由 Evidence Runner 生成）
- verdict: pass | fail（所有必需 checks 退出码=0 即 pass）
- metrics: 自由键值（由各 checks 解析日志或自报）
- logs: `LOG:runner#Lx-Ly`、`LOG:<tool>#Lx-Ly` 等引用，指向 `.cccc/work/logs/**` 内的稳定文本片段。
- promotion（可选）：将持久化工件晋升到 `docs/evidence/**` 并记录来源、哈希、大小。

四、原则
- 与领域无关：不预设“证据类型注册表”；任何场景均可用 artifacts+checks 表达。
- 少而硬：证据通过才能改变系统状态；危险操作走 RFD；≤150 行或等效粒度小步推进。
- 可回放：所有运行日志入账，摘要在 IM 中仅给出四行与引用，不直贴长文。

五、示例（调研引用可用性）
```yaml
kind: research.citations
title: "LangChain RAG 实践资料初筛"
artifacts:
  - path: docs/research/notes.md
  - path: https://arxiv.org/abs/2307.09288
  - path: https://blog.langchain.dev/rag-2024/
checks:
  - name: link-probe
    run: "python3 tools/link_probe.py docs/research/notes.md --out .cccc/work/logs/links.txt"
    timeout_sec: 30
    allow_failure: false
  - name: snapshot
    run: "python3 tools/snapshotter.py --list docs/research/notes.md --dest docs/evidence/snapshots"
    timeout_sec: 120
    allow_failure: true
meta:
  min_alive_ratio: 0.9
```

六、与编排器的关系
- Evidence Runner 仅是“执行与取证”工具；是否合入/晋升由队列与门控决定。
- 事件入账：`kind: evidence-validate`，记录 verdict/metrics/log_refs 与 artifacts 摘要。

