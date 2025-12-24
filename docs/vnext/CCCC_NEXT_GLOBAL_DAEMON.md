# CCCC vNext：全局实例 + Working Groups + Scopes（规划草案）

> 目标：把 CCCC 从“每个仓库一份 `.cccc/` 运行目录 + 入口各自持状态”升级为**全局唯一的交付协作中枢**：一个常驻实例管理多个 working group（工作群组），每个 group 绑定一个明确的工作根目录（MVP：单一 project root；未来可扩展为 scopes 目录集合），Web/IM/CLI 都只是入口（port）。

实现进度与剩余缺口请看：`docs/vnext/STATUS.md`

关于“如何让 agents 明白自己角色/边界/操作规程（在拿不到 system prompt 常驻权限时）”的最终取舍与落地要点，请看：`docs/vnext/AGENT_GUIDANCE.md`

## 0. 一句话结论

- **是的**：vNext 的运行时状态/日志/账本等工作文件，统一落在**全局的 CCCC Home**（默认 `~/.cccc/`），而不是每个仓库各自维护一份 `.cccc/`。
- 用户层面只有一个核心概念：**Working Group（工作群组）**。它像 IM 的群聊，但具备“执行/交付/可恢复”的结构化能力。
- MVP：每个 group 有一个 **Project Root（项目根目录）**，用于决定 actors 的启动目录与相对路径语义（这是最低摩擦、最高 ROI 的核心）。
- 未来（跨仓库协作）才扩展到多个 **Scopes（工作范围）**：本质是“若干目录/仓库的 URL（通常是本地路径）”。
- scopes 不必作为独立的“产品对象”高频暴露；但系统内部必须把“执行作用域”记录清楚（否则无法稳定重放、恢复与审计）。

## 1. 为什么必须做“全局实例”

从第一性原理看，`cccc` 要变强，关键不是 UI，而是**状态一致性与可恢复性**：

- **单一事实源（single source of truth）**：Web/IM/CLI/headless 同步依赖同一份事实流，否则入口越多越乱。
- **可恢复（restartable）**：守护进程崩溃/重启后要能从事实流恢复状态。
- **跨仓库协作**：未来“同一批 agent 同时推进多个仓库”需要统一调度与视图。

这些目标天然更适合“全局 daemon + group/scope 隔离”，而不是“每仓库一个自包含目录 + 入口各自持状态”。

## 2. vNext 的产品形态（可见的终局）

### 2.1 一个全局常驻实例：`ccccd`

- `ccccd` 是**全局唯一**的后台进程（daemon），管理：
  - 多个 working group 的事件流/状态
  - actor（agent/角色）生命周期（启动/停止/扩编/解散）
  - 交付与通知（ports/出站等）

### 2.2 一个核心对象：Working Group（协作 + 治理），Project Root 作为其执行根目录（MVP）

**Working Group（工作群组）**
- 解决“围绕什么目标协作”的问题：对话、任务协作、决策点、证据引用、交付节奏。
- group 是 UI/IM 的一等对象（像群聊），也是 ledger 的归属单位。

**Project Root（项目根目录）**
- 解决“actor 在哪启动/相对路径基于哪里”的问题：它是本项目最关键的摩擦点。
- vNext MVP：每个 group 只要求 1 个 project root（目录 URL/路径），先把体验做顺，再谈跨仓库。

**Scopes（工作范围 / working scopes，后置能力）**
- 解决“同一 group 覆盖多个仓库/目录”的问题：一个 group 可以绑定多个 scopes（天然支持跨仓库协作）。
- scopes 的最小定义就是“目录 URL 列表”（初期只支持本地路径 + 可选 git remote）。
- 系统执行任何会影响文件/命令的动作时，都必须在事件里标注对应 scope（否则无法稳定复盘与追责）。

### 2.3 Web/IM/CLI 都是 Port（不再决定真相）

- Web：现代 IM 风格的“主控制台”（local-first，remote-ready），用于多 group 统一视图与低摩擦操作。
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
    ccccd.sock                 # 本地 IPC（unix socket）
  groups/
    <group_id>/
      group.yaml               # 元数据：title、project root、actors、tags 等
      ledger.jsonl             # 事实流（事件/消息/决策/对话），单写者 append-only
      context/                 # 该 group 的 ccontext 数据目录（默认：统一收归 CCCC_HOME）
      state/                   # 可重建但用于加速/运行时
      scopes/
        <scope_key>/
          scope.yaml           # scope 元数据：root(s)、git remote、label 等
          runners/             # runner 的运行信息（pty/headless 等）
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
- **`scope_key`**：系统内部存储/目录名/ledger 索引键（短、稳定；`scope_key` 本身不包含敏感路径）。

注意（现实取舍）：
- scope 元数据仍会保存 URL（通常是绝对路径）在 `group.yaml` 与 `scopes/<scope_key>/scope.yaml`，用于 runner 启动目录与可恢复性。
- 如需“路径脱敏/可迁移引用”，应作为后置能力（例如 alias/映射表 + 可选 redaction），不要在 vNext MVP 阶段卡死可用性。

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

## 6. Actor CLI 启动位置：默认在 Project Root（MVP 单根目录）

结论（高 ROI 默认）：
- **默认在 group 的 project root 启动 actor CLI**，而不是在 `CCCC_HOME` 启动。

原因：
- agent 的绝大多数动作围绕“相对路径 + git + 测试/构建命令”展开；把 cwd 放在 project root 可显著降低摩擦与误操作概率。
- 未来跨 scope 协作并不要求“一个进程同时在多个 cwd 工作”；更稳健的方式是：一个 actor 实例绑定一个 root，需要跨 scope 时启动第二个 actor 或显式切换（后置能力）。

实现要点（不增加复杂度但保证可审计）：
- 每个 actor instance 有固定 `group_id` 与其 workdir（MVP：project root），并且 runner/事件里记录对应的 root/scope_key。
- 不强行重写 `HOME`；默认继承系统 `HOME`（避免破坏凭证/插件/本地工具链）。
- 可以在不破坏体验的前提下，按需把 cache/log 定位到 `CCCC_HOME`（例如通过 `XDG_CACHE_HOME` / `XDG_STATE_HOME` 指向 `~/.cccc/cache/actors/<actor_id>`），但这属于优化项，不是 MVP 前置。

入口与选择器语义（为了避免越做越糊涂）：
- `cccc`（无参数）是全局工作台入口：**不读取 cwd、不隐式选择 group、不隐式修改 project root**。
- 所有写操作必须显式绑定 group：`--group <id>` 或 “active group”（由用户明确 `cccc use <id>` / UI 切换）。
- cwd 只允许作为“用户输入 project root 时的候选值/预填”，绝不用于推断“要操作哪个 group”。

project root 缺失时的体验（低摩擦但不隐式）：
- 当对某 group 执行 `start` / `actor start` 但未设置 project root：返回明确错误（例如 `missing_project_root`），由 Web/CLI 提示用户设置一次（设置后需要重启对应 actor 才能生效）。

安全与灵活性的取舍（vNext MVP 默认）：
- 先不做“硬拦截式”的路径护栏（例如强制校验执行路径必须落在 root/scope roots 下），避免把框架的灵活性卡死。
- 但必须保证“可理解/可恢复”：所有执行/交付动作都要明确标注当前 root/scope_key，并在 UI/日志里可见当前 active root。

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

当前 repo 结构（截至目前实现）：

```
src/
  cccc/
    __init__.py
    cli.py                     # `cccc` CLI（port）：通过 IPC 读写 daemon；不可用时有本地 fallback
    daemon_main.py             # `ccccd` 入口
    paths.py                   # CCCC_HOME 解析与路径工具

    contracts/                 # v1 契约层（Pydantic models；尽量保持小而稳定）
      v1/
        actor.py
        event.py
        ipc.py
        message.py

    kernel/                    # 内核：group/scope/ledger/inbox/permissions/system-prompt 等
      group.py
      scope.py
      registry.py
      ledger.py
      ledger_retention.py
      inbox.py
      actors.py
      permissions.py
      system_prompt.py
      active.py
      git.py

    daemon/                    # ccccd：IPC + supervision + delivery/automation
      server.py
      delivery.py
      automation.py

    runners/
      pty.py

    ports/
      web/                     # web port（FastAPI + bundled UI + WS terminal）
        app.py
        main.py
        dist/

    resources/                 # 内置资源（尽量只读；后续可用于模板/默认配置）

    util/
      fs.py
      time.py
```

分层依赖规则（“经得起考验”的关键）：
- `contracts` 是稳定边界（当前用 Pydantic 表达 schema），尽量避免引入业务实现与 port 细节。
- `kernel` 依赖 `contracts`；不依赖具体 ports（Web/IM）与 runner 实现细节。
- `daemon` 依赖 `kernel` + `runners`；不依赖具体 UI（Web/IM）。
- 目标形态：`ports/*` 只通过 IPC 与 `daemon` 交互，避免多写者导致的漂移。
- MVP 允许：当 daemon 不可用时，CLI 可以做有限的本地写入（dev convenience），但后续应逐步收敛到单写者。

## 9. 迭代路线（不考虑向后兼容的最短路径）

### Phase 1：先做“全局 home + group/scope 基础骨架”
- ✅ `CCCC_HOME`、`registry.json`、`groups/<group_id>/` 初始化
- ✅ `group.yaml`（running、scopes、active scope、actors）、`groups/<group_id>/ledger.jsonl` 初始化
- ✅ `ccccd` 能启动/停止、能 attach scope、能写最小事件到 group ledger
- ✅ IPC 契约（v1 request/response）+ append-only 事件（v1 event/message）
- ✅ active group（减少日常摩擦：send/tail 默认走 active group）
- ✅ runner 恢复与崩溃清理：PTY pidfile + daemon 启动时清理孤儿进程 + autostart `running=true` 的 groups

### Phase 2：把 UI/IM/CLI 统一到“发命令 + 读 ledger”
- Web 只做工作台（不再直接读写散落的 state 文件）
- IM 入口与输出同源（都落账）
- ✅ CLI 已具备基础 port 行为：send/tail/use/actor/inbox/read（通过 IPC 或本地 fallback）

### Phase 2b：Web 控制台（local-first，remote-ready）
目标：提供“2025 质感”的 IM 风格控制面，但不引入双写者/双状态源。
- **定位**：Web 只是一个 Port（入口），daemon+ledger 仍是唯一真相；Web 不直接写文件，不维护影子状态。
- **衔接方式（关键）**：
  - Browser 端只会说 HTTP/WebSocket，不能直接连 `ccccd` 的 unix socket。
  - 增加一个 `cccc-web`（或 `ccccd` 内置的 web port），对外提供：
    - REST：group/actor/scope/send/mark-read 等控制面操作（最终都转成 `ccccd` op）
    - SSE（或 WebSocket）：订阅 group 的新事件/状态变更（由 daemon 推送或由 web port tail ledger）
  - Web port 与 `ccccd` 的通信走**本地 IPC**（复用现有 unix socket），确保“单写者”不被破坏。
- **默认只监听 localhost**：`127.0.0.1`；远程访问不强行内置外网穿透，优先推荐 Tailscale / Cloudflare Tunnel（部署策略后置）。
- **移动端前景**：Web UI 做成响应式 + PWA（优先满足“随时查看/下指令/审批/确认”；终端接管可做成可选 fallback，但需要专门的移动端输入与快捷键设计）。

建议把 Phase 2b 拆成三个可验收的增量（避免“做完变味”）：

#### Phase 2b.1：IM 风格控制台（结构化视图优先）
- group 列表 + group 详情（事件流/对话流/决策点/引用）
- send 支持多收件人（actor id/title、`user/@user`、`@all/@peers/@foreman`；`@role:*` 作为后置扩展），并能在 UI 中一键选择
- actors / scopes / runners 的状态可见，并可做高频操作（spawn/stop/attach scope/切 active scope）
- 关键原则：所有操作最终都落到 group ledger（Web 不维护独立状态）

#### Phase 2b.2：Web Terminal 接管（PTY + xterm.js）
目标：避免“Web 控制台 + 本地终端”两套体验长期并存，把“人类随时介入 actor CLI”合并进 Web。
- 每个 actor 绑定一个 daemon-managed PTY session；Web 通过 WebSocket attach/detach
- Writer arbitration（后置）：MVP 暂不做“抢占/释放”，默认同一时刻只有一个前端写入；需要多前端并发时再引入单写者机制
- 可靠性必做：断线重连、resize、server-side scrollback、背压/限速、复制/粘贴、快捷键策略
- 移动端必做：提供屏幕快捷键栏（Esc/Ctrl/Tab/↑↓←→/PgUp/PgDn），并正确处理中文/IME composition

#### Phase 2b.3：PWA / App 化（同一套前端代码）
- PWA：可安装、响应式布局、移动端 safe-area 适配；离线能力先做“只读缓存”即可（避免双写）
- 未来如需“真 App”：优先用 Capacitor（iOS/Android）或 Tauri（桌面）做壳，复用同一份 Web UI 构建产物

#### Phase 2b 实现技术栈（2025 年末经得起考验的基线）
目标：避免双后端/双状态源/双 UI 长期并存，把复杂度集中到“PTY 会话模型 + 事实流（ledger）”。

- **Kernel/Daemon**：Python（保留现有 `ccccd + ledger + contracts` 资产；把风险留给产品模型而不是语言重写）
- **Web Port（控制面）**：FastAPI（Starlette）+ Uvicorn
  - REST：group/scope/actor/send/mark-read 等（最终转为 `ccccd` 操作）
  - SSE：订阅 ledger/event（单向流，移动端更稳）
  - WebSocket：终端接管（双向低延迟）
- **Web UI**：React + TypeScript + Vite（local-first；不引入 Node 服务器侧）
  - UI primitives：shadcn/ui（Radix）+ Tailwind（便于做 IM 细节与一致性）
  - 长列表：react-virtuoso（消息流/事件流必须虚拟化）
  - 数据层：TanStack Query（其余状态尽量少；必要时再上轻量 store）
- **Web Terminal**：xterm.js（含 fit/search/web-links 等 addons）+ WS
  - MVP：daemon-managed PTY runner + WebSocket attach（先把“介入同一条终端会话”做稳）
  - Phase 3：补齐背压/IME/锁/scrollback，并做移动端输入与快捷键的专门打磨
- **PWA / App**：
  - PWA：同一套 Web UI 直接可安装（优先满足查看/指令/审批；终端接管做可选 fallback）
  - 真 App：优先 Capacitor（iOS/Android）复用 Web UI；桌面壳如有需要再评估 Tauri

明确不做/后置（为了减少弯路）：
- 不做 Next.js 这类全栈/SSR 作为 MVP（会引入第二套服务心智与更复杂的部署形态）
- 不做 Node 后端作为控制面（避免双后端与契约漂移；Python+Pydantic 已是最佳路径）

### Phase 3：Runner 抽象（PTY 会话模型 + headless）
- 优先实现 pty/headless runner（为 Web Terminal 提供持久会话基础），为 N agents 与无人值守铺路

### Phase 4：Actor/Role 编排（可扩编、可解散、群组寻址）
- ✅ actor registry + 权限（group 内最多 1 foreman；foreman 可管理 peers；peer 只能操作自己）
- ✅ inbox/read（面向 MCP 的“取消息 + 已读标记”基础语义）
- ⏭️ 支持 `@all/@peers/@foreman` + actor title 的完整寻址与解析，并逐步扩展到自定义 roles/tags（例如 `@role:<name>`）
- ⏭️ 生命周期管理成为一等能力（spawn/scale/down，runner+supervisor 驱动）

---

**这份文档只固化“形态与边界”，不固化实现细节。**后续讨论会围绕：ledger 的最小字段集、actor/role 模型、headless runner 方案与工程目录重建。

补充：ledger 的 v1 schema 见 `docs/vnext/LEDGER_SCHEMA.md`。
