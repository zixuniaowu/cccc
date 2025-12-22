# CCCC vNext：全局实例 + Working Groups + Scopes（规划草案）

> 目标：把 CCCC 从“每个仓库一份 `.cccc/` 运行目录 + 强依赖 tmux/TUI”升级为**全局唯一的交付协作中枢**：一个常驻实例管理多个 working group（工作群组），每个 group 绑定一个或多个执行 scope（工作范围/目录集合），TUI/IM/CLI 都只是入口（port）。

## 0. 一句话结论

- **是的**：vNext 的运行时状态/日志/账本等工作文件，统一落在**全局的 CCCC Home**（默认 `~/.cccc/`），而不是每个仓库各自维护一份 `.cccc/`。
- 用户层面只有一个核心概念：**Working Group（工作群组）**。它像 IM 的群聊，但具备“执行/交付/可恢复”的结构化能力。
- 每个 group 维护一个或多个 **Scope（工作范围）**：本质是“若干目录/仓库的 URL（通常是本地路径）”，用于界定命令与改动发生在哪些工作根目录中。
- scope 不必作为独立的“产品对象”高频暴露；但系统内部必须把“执行作用域”记录清楚（否则无法稳定重放、恢复与审计）。

## 1. 为什么必须做“全局实例”

从第一性原理看，`cccc` 要变强，关键不是 UI，而是**状态一致性与可恢复性**：

- **单一事实源（single source of truth）**：TUI/IM/headless 同步依赖同一份事实流，否则入口越多越乱。
- **可恢复（restartable）**：守护进程崩溃/重启后要能从事实流恢复状态。
- **跨仓库协作**：未来“同一批 agent 同时推进多个仓库”需要统一调度与视图。

这些目标天然更适合“全局 daemon + group/scope 隔离”，而不是“每仓库一个自包含目录 + 入口各自持状态”。

## 2. vNext 的产品形态（可见的终局）

### 2.1 一个全局常驻实例：`ccccd`

- `ccccd` 是**全局唯一**的后台进程（daemon），管理：
  - 多个 working group 的事件流/状态
  - actor（agent/角色）生命周期（启动/停止/扩编/解散）
  - 交付与通知（tmux paste/IM 出站等）

### 2.2 一个核心对象：Working Group（协作 + 治理），Scope 作为其内部工作范围

**Working Group（工作群组）**
- 解决“围绕什么目标协作”的问题：对话、任务协作、决策点、证据引用、交付节奏。
- group 是 UI/IM 的一等对象（像群聊），也是 ledger 的归属单位。

**Scope（工作范围 / working scope）**
- 解决“在哪些目录里执行/改代码”的问题：一个 group 可以绑定多个 scope（天然支持跨仓库协作）。
- scope 的最小定义就是“目录 URL 列表”（vNext 初期可只支持本地路径 + 可选的 git remote 信息）。
- 系统执行任何会影响文件/命令的动作时，都必须在事件里标注对应 scope（否则无法稳定复盘与追责）。

### 2.3 TUI/IM/CLI 都是 Port（不再决定真相）

- TUI：最主要的“工作台”，用于发命令、看状态/事件流。
- IM：远程控制与通知渠道。
- CLI：脚本/CI/快速操作。
- 它们都不再各自维护权威状态，所有关键行为必须落到 group 的事实流（ledger）里。

## 3. 全局目录布局（建议）

默认：`CCCC_HOME=~/.cccc`

```
~/.cccc/
  registry.json                # working groups 索引（group_id、title、最近活跃等）
  daemon/
    ccccd.pid
    ccccd.log
    sockets/                   # 可选：本地 IPC（unix socket）
  groups/
    <group_id>/
      group.yaml               # 元数据：title、scopes、tags、默认 scope 等
      ledger.jsonl             # 事实流（事件/消息/决策/对话），单写者 append-only
      context/                 # 该 group 的 ccontext 数据目录（默认：统一收归 CCCC_HOME）
      state/                   # 可重建但用于加速/运行时
      scopes/
        <scope_key>/
          scope.yaml           # scope 元数据：root(s)、git remote、label 等
          runners/             # tmux/pty/subprocess runner 的运行信息
          logs/
          work/                # 该 scope 下的临时产物（可清理）
```

原则：
- `groups/*/ledger.jsonl` 作为“可回放事实流”，尽量**只追加**，并作为 UI/IM/headless 的统一视图来源。
- `state/` 可在重启时由 ledger 重建（加速用，不是权威）。
- `work/` 属于临时证据/中间产物（可按策略归档/清理）。

## 4. Scope 标识：URL（对人）+ `scope_key`（对系统）

目标：既保持“对人好用”（直接用目录 URL/路径理解 scope），又让系统具备稳定的索引键（避免路径字符、长度、隐私、跨机器差异导致的麻烦）。

约定：
- **URL/路径**：用户输入/显示用（例如 `/home/me/repo` 或 `file:///home/me/repo`）。
- **`scope_key`**：系统内部存储/目录名/ledger 索引键（短、稳定、无敏感路径泄漏）。

建议策略（vNext 最小可用）：
- 若 scope 是 git 仓库：优先用 `remote.origin.url`（规范化后）生成 `scope_key`（hash/slug）。
- 若 scope 只是普通目录：用目录路径生成一次性 `scope_key`（hash），并记录原始 URL 在 `scope.yaml`。
- 支持 `label`（人类可读）用于 UI 展示，但 **`scope_key` 不以名字为准**。

## 5. 与 ccontext 的关系（职责边界）

最稳健的分工是：
- **ccontext = 上下文真相**（vision/sketch/milestone/task/notes/refs 的结构与内容）
- **cccc = 协作运行真相**（actor/runner/交付/通知/决策点/证据引用的事实流）

关于“ccontext 放哪里”（已与 vNext 形态对齐）：
- vNext 默认把上下文真相收归 `CCCC_HOME`：`~/.cccc/groups/<group_id>/context/`。
- 这样不会把 context 文件侵入到各个仓库中，同时 ccontext 仍可独立使用（它只需要一个 context 目录路径即可）。

ccc 的 ledger 里只需要保存：
- “发生了什么”（事件/消息/决策）
- “引用什么证据”（refs 到文件/commit/URL/artifact）
- “上下文指针/变更摘要”（必要时写 snapshot id 或变更说明）

避免把上下文全文复制进 ledger，防止双写漂移与同步成本爆炸。

## 6. Actor CLI 启动位置：默认在 Scope 根目录

结论（高 ROI 默认）：
- **默认在 scope 的工作根目录启动 actor CLI（通常是 repo root）**，而不是在 `CCCC_HOME` 启动。

原因：
- agent 的绝大多数动作围绕“当前仓库的相对路径 + git + 测试/构建命令”展开；把 cwd 放在 scope 根目录可显著降低摩擦与误操作概率。
- 跨 scope 协作并不要求“一个进程同时在多个 cwd 工作”；更稳健的方式是：**一个 actor 实例绑定一个 scope**，需要跨 scope 时显式切换 active scope 或启动第二个 actor。

实现要点（不增加复杂度但保证可审计）：
- 每个 actor instance 有固定 `group_id` 与 `scope_key`，并且 runner/事件里都记录 `scope_key`。
- 不强行重写 `HOME`；默认继承系统 `HOME`（避免破坏凭证/插件/本地工具链）。
- 可以在不破坏体验的前提下，按需把 cache/log 定位到 `CCCC_HOME`（例如通过 `XDG_CACHE_HOME` / `XDG_STATE_HOME` 指向 `~/.cccc/cache/actors/<actor_id>`），但这属于优化项，不是 MVP 前置。

安全与灵活性的取舍（vNext MVP 默认）：
- 先不做“硬拦截式”的路径护栏（例如强制校验执行路径必须落在 scope roots 下），避免把框架的灵活性卡死。
- 但必须保证“可理解/可恢复”：所有执行/交付动作都要明确标注当前 `scope_key`，并在 UI/日志里可见当前 active scope。

## 7. MCP 策略：不合并仓库，但合并“工具面”

结论（最专业的折中）：
- `cccc` 需要一个 **Control MCP**（控制面）让 agent 能管理 group/scope/actor/runner。
- `ccontext` 仍然有价值（上下文真相的通用工具），但 **不建议把两个仓库硬合并**，会把通用上下文工具与 cccc 内核耦合，反而降低可复用性与演进速度。

推荐落地方式：
- 提供一个 `cccc-mcp`（单一 MCP 服务）对外暴露两组工具：
  - `context.*`：复用/嵌入 `ccontext` 的语义与数据结构（同 schema），直接读写 `CCCC_HOME/groups/<group>/context/`。
  - `cccc.*`：面向 `ccccd` 的控制面（创建 group、绑定 scope、spawn/stop actor、设置 active scope、发消息/命令等）。
- 在 `cccc` 使用场景下，用户只需安装/启用 `cccc-mcp`（降低摩擦）。
- `ccontext-mcp` 继续作为“脱离 cccc 也可用”的独立项目存在（保持产品边界清晰）。

## 8. Repo 代码目录结构（vNext 建议）

结论：
- vNext 不再把“核心源码”放在仓库根目录的隐藏目录（如旧版 `.cccc/`）里。
- **`.cccc` 这个名字在 vNext 应保留给运行时 home（`~/.cccc`）**，避免“源码 `.cccc/` vs 运行时 `~/.cccc/`”的长期混淆。
- 源码采用标准 Python package 结构（推荐 `src/` layout），保证可测试、可打包、可分层。

推荐目录树（示意）：

```
src/
  cccc/
    __init__.py
    cli.py                     # `cccc` 入口：发命令/启动 daemon/attach 等
    daemon_main.py             # `ccccd` 入口

    contracts/                 # 稳定“契约层”（最好版本化：v1/）
      v1/
        command.py             # intent: command schema
        event.py               # fact: event schema
        message.py             # chat.message schema
        selectors.py           # @all/@role/... 选择器语义
        refs.py                # evidence refs 结构

    kernel/                    # 内核：不关心 UI/runner，实现 group/scope/ledger 基础
      home.py                  # CCCC_HOME 解析与路径
      registry.py              # groups 索引/持久化
      group.py                 # group.yaml 模型与读写
      scope.py                 # scope.yaml 模型与读写
      ledger/
        writer.py              # append-only 单写者
        reader.py              # tail/query
        index.py               # 可选：加速索引（非权威）

    daemon/                    # ccccd：命令处理/恢复/调度/监督
      server.py                # IPC server（unix socket）
      dispatcher.py            # command -> actions
      recovery.py              # 从 ledger 重建 state
      supervisor.py            # actor 生命周期与 runner 调度

    runners/                   # runner 抽象：tmux/headless 作为实现
      base.py
      tmux.py
      pty.py

    ports/                     # 入口（port）：都只发命令/读 ledger
      tui/
        app.py
      im/
        telegram.py
        slack.py
      mcp/
        server.py              # `cccc-mcp`（可选，或拆独立 repo）

    resources/                 # 默认模板/提示词/内置 schema（只读）
      defaults/
        group.yaml
        scope.yaml
```

分层依赖规则（“经得起考验”的关键）：
- `contracts` 只依赖 stdlib/类型定义：它是未来多语言重写的稳定边界。
- `kernel` 依赖 `contracts`；不依赖 `ports`/`runners`。
- `daemon` 依赖 `kernel` + `runners`；不依赖具体 UI（TUI/IM）。
- `ports/*` 只通过 IPC 与 `daemon` 交互；禁止绕过 daemon 直接写 ledger/state（防止多写者）。

## 9. 迭代路线（不考虑向后兼容的最短路径）

### Phase 1：先做“全局 home + group/scope 基础骨架”
- `CCCC_HOME`、`registry.json`、`groups/<group_id>/` 初始化
- `group.yaml`（至少含 scopes 与默认 scope）、`groups/<group_id>/ledger.jsonl` 初始化（默认 group = 当前 repo）
- `ccccd` 能启动、能 attach 当前仓库到 group/scope、能写最小事件到 group ledger

### Phase 2：把 UI/IM/CLI 统一到“发命令 + 读 ledger”
- TUI 只做工作台（不再直接读写散落的 state 文件）
- IM 入口与输出同源（都落账）

### Phase 3：Runner 抽象（tmux 变成实现之一）
- 保留 tmux runner（本地体验）
- 引入 headless runner（pty/subprocess），为 N agents 与无人值守铺路

### Phase 4：Actor/Role 编排（可扩编、可解散、群组寻址）
- 支持 `@all/@peers/@role:*` 的寻址与解析
- 生命周期管理成为一等能力（spawn/scale/down）

---

**这份文档只固化“形态与边界”，不固化实现细节。**后续讨论会围绕：ledger 的最小字段集、actor/role 模型、headless runner 方案与工程目录重建。
