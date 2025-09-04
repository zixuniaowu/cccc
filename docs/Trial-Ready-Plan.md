# CCCC 可推广试用版迭代规划（M2.1–M2.3）

本规划旨在把当前“脚手架可用”的编排器打磨为“新人 5 分钟内可跑通”的试用版，同时坚持证据优先、安全最小化和可追溯治理（RFD）。

## 目标与原则
- 目标：
  - 新用户在 5 分钟内完成“最小补丁 → 预检 → 合入 → 账本可证（ledger）”的首次成功；Telegram 可选。
  - 默认英语界面与 README；PyPI 安装路径可用；仓库不含密钥。
- 原则：
  - 小步可验证：单分支、小改动、预检先行、证据落账。
  - 安全最小化：密钥不进仓库；默认 dry-run；路径与域边界清晰。
  - 零配置默认：无额外编辑亦可本地演示；逐步暴露高级功能。

## 里程碑与顺序（建议）
\n+### 默认模式：Ephemeral（仅此一种，当前阶段）
- 定义：目标业务仓库中的 `.cccc/**` 视为“工具/运行域”，不进入该仓库的版本控制；可随时重建与清理。
- 忽略规则（由 `cccc init` 自动追加到目标仓库根 `.gitignore`）：
  - `/.cccc/**`
  - 说明：本产品仓库（CCCC 项目仓库）不应用该规则；仅在目标业务仓库生效。
- Token 持久化：
  - 存放于目标仓库 `.cccc/settings/telegram.yaml` 的 `token` 字段（默认保存一次，供下次使用）。
  - 该文件不进入版本控制（被 `.gitignore` 覆盖）。也可继续使用环境变量临时传入。
- 运行行为：
  - `cccc run`：真实执行 orchestrator；仅当检测到 token 时才连接 Telegram；否则不启用 bridge（不使用 dry-run，除 `demo` 专用）。
  - `cccc demo`：用于演示链路时可启用 bridge 的 dry-run；不影响真实聊天。
  - `cccc clean`：清理 `.cccc/{mailbox,work,logs,state}/`，随时可恢复。

### M2.1 基础整洁与最小体验闭环（优先）
- 为什么：降低学习/操作负担；把“能跑通”的路径缩至两步。
- 关键改动：
  - 仓库清理：移除运行时产物与历史噪声；仅保留结构与示例。
  - `.gitignore` 扩展：忽略 `.cccc/{state,logs,mailbox,work}/**` 与 `.cccc/settings/telegram.yaml`。
  - CLI/UX：
    - `cccc init --quickstart`：写入最小脚手架；在目标仓库根 `.gitignore` 追加 `/.cccc/**`（若非本产品仓库）。
    - `cccc run`：启动 orchestrator，默认本地模式。
    - `cccc demo`：一键跑通“最小补丁→预检→合入→ledger tail”。
    - `cccc clean`：安全清理 `.cccc/{mailbox,work,logs,state}/`。
    - `cccc --help`：任务型帮助与可复制示例。
  - Doctor：`cccc doctor` 检测 git/tmux/python/keyring/telegram（dry-run），输出可操作修复建议。
- 验收：
  - TTFP（首次产出补丁）≤ 2 分钟；命令 ≤ 2 个（init → demo）。
  - `git status` 干净；`--help` 清晰可复制。
- 指标：
  - 新人“首次成功路径”命令数 ≤ 2；失败率 < 5%。

### M2.2 密钥与桥接可靠性
- 为什么：避免反复输入 token；桥接更稳健且可自诊断。
- 关键改动：
  - Token 持久化（不入版本库）：默认保存到目标仓库 `.cccc/settings/telegram.yaml`（字段：`token`）。
    - CLI：`cccc token set|unset|show`（对该文件进行增删查）。
  - 桥接子命令：`cccc bridge start|stop|status`；仅当检测到 token 时进入 live；否则给出设置指引。
  - 可靠性：网络抖动重试、清晰日志、状态自检；最小 `/rfd list|show`（读 ledger 尾部）。
- 验收：
  - 设置一次 token 后，`cccc run` 无需再次输入；repo 无密钥文件。
  - dry-run 可完整体验；live 模式稳定；错误有指引。
- 指标：
  - 因 token 导致启动失败≈0；桥接 30 分钟无致命错误。

### M2.3 全英文化 + README 重构 + PyPI 发布
- 为什么：降低传播与安装门槛，面向更广试用者。
- 关键改动：
  - 英文化：CLI 输出、默认系统提示与日志统一英文（保留 zh 文档可选）。
  - README（EN）重构：
    - What/Why：证据优先、双 AI 协作、RFD 治理。
    - 90 秒 Quickstart（pipx install → cccc init → cccc demo）。
    - Governance：CLAIM/COUNTER/EVIDENCE、RFD 卡片与 ledger。
    - Verify：如何在 ledger/test/log 中验证证据。
    - Telegram 设置与最小命令；安全边界；限制与路线图。
  - PyPI 打包：`pyproject.toml` + console_scripts `cccc`；包含 `.cccc` 脚手架为 package data；CI 冒烟（install→demo）。
- 验收：
  - `pipx install cccc-orchestrator` → `cccc init --quickstart` → `cccc demo` 一次性跑通。
  - README 跟着操作 5 分钟内产出一次 patch-commit 与 ledger 证据。
- 指标：
  - 安装失败率 < 5%；“首次成功路径”命令 ≤ 3。

## 完成定义（DoD：试用版）
- 无 Telegram：3 条命令内完成 init、demo，产出补丁→预检→合入→ledger 条目。
- 有 Telegram：`cccc token set` 后 `cccc bridge start` 成功；可收到 to_user 摘要与一张 RFD 卡片（按钮写回 decision）。
- 安全：任何 repo 下不出现密钥；`.cccc/settings/telegram.yaml` 默认不含 token。
- 文档：README（EN）与 CLI 帮助一致、可复制、可复现。
- 包装：PyPI 包含所需资产；安装与 `demo` 冒烟通过。

## 风险与缓解
- tmux 依赖：`cccc doctor` 明确提示并提供安装命令；考虑“bridge‑only”降级路径（后续）。
- keyring 不可用：回退 `~/.config/cccc/credentials.yaml`（0600），并提示权限。
- 打包遗漏：CI 进行 install→demo 冒烟，防止缺失资源。
- 国际化：默认 EN；zh 文档为补充，降低双语维护成本。

## 工作量与节奏（粗估）
- M2.1：1–2 天（清理 + help/doctor/demo + quickstart/clean）。
- M2.2：1–2 天（keyring + token CLI + bridge 稳健化 + 最小 rfd list）。
- M2.3：1–2 天（英文化 + README 重构 + 打包与 CI 冒烟）。

## 与当前缺口的对应（对齐产品关切）
- 粗糙/废文件：M2.1 清理、gitignore、`cccc clean`。
- 开箱体验：M2.1 的 demo/doctor/help；M2.2 的 token 持久化与 dry‑run 兜底。
- token：M2.2 采用 keyring/用户级 config；不入仓库。
- README：M2.3 重构，讲清楚“目的→治理→如何验证→快速成功”。
- 推广：M2.3 英文化与 PyPI，降低试用门槛。

## 附录：命令与配置草案（不改变现有行为，增量补充）
- 命令：
  - `cccc init [--quickstart] [--to PATH]`：生成脚手架；追加 gitignore 片段。
  - `cccc run`：运行 orchestrator（默认 dry-run 模式）。
  - `cccc demo`：最小演示；自动生成并合入一个示例补丁；显示 ledger tail。
  - `cccc doctor`：诊断环境与依赖，输出修复建议。
  - `cccc clean`：清理 `.cccc/{state,logs,mailbox,work}/`。
  - `cccc token set|unset|whoami`：管理 Telegram token（keyring 优先，回退用户级配置）。
  - `cccc bridge start|stop|status`：控制桥接；按 token 自动选择 live/dry-run。
- 配置：
  - 用户级：`~/.config/cccc/credentials.yaml`（0600，非必须，密钥仅在 keyring 不可用时写入）。
  - 项目级：`.cccc/settings/*` 仅存放非敏感项；整个 `.cccc/` 作为运行域，不作为业务交付物。

> 注：本规划为增量收敛，不大改核心 orchestrator 设计；优先交付可验证的“首次成功路径”，再做可选增强。
