# CCCC vNext — Agent Guidance（System Prompt / Skills / MCP 的取舍与落地）

> 目的：记录一套**经得起考验**的“让 agents 明白怎么在 CCCC 里工作”的方案，避免未来反复摇摆与文案漂移。
>
> 适用场景：同一台机器/同一 group 中可能同时启动多个长会话 actors（例如 foreman + 多个 peers），且 actors 可能来自不同 agent runtimes（Claude Code / Codex / OpenCode 等）。

## 0) 一句话结论（Do / Don’t）

- **Do：一个权威规程（Playbook）**：用一个核心 skill（建议名：`cccc-ops`）承载长规程与最佳实践，作为“怎么做”的唯一真相。
- **Do：内核强约束（RBAC）**：任何“会改变世界状态/结构”的动作必须由 `ccccd`（或其工具面）做硬校验；不要靠提示词/skills 说服模型。
- **Do：极短启动握手（Bootstrap）**：我们拿不到“系统提示词常驻权限”，就用启动时的一条短消息告诉 actor：你是谁、你的边界、规程入口。
- **Don’t：三套文案各写一遍**：不要把同一套 HOWTO 分别写进 system prompt、skill、MCP tool description。长规程只放 skill；其它地方只放指针。

## 1) 我们的真实约束（为什么不能只靠 system prompt）

### 1.1 “系统提示词级别信息”确实必要，但我们拿不到常驻

我们希望 agents 稳定理解：
- 角色分工与权限边界（谁能创建/删除结构、谁负责治理、谁只负责执行/更新）。
- 交付与沟通格式（handoff/self-check/汇报要点）。
- 在 CCCC 里应该调用哪些机制（消息、ledger、actor 生命周期等）。

这些属于“系统提示词级别”的 *规则*，但现实是：
- 很多 agent runtime/CLI 不允许我们写入真正的 system prompt，或其持久性不可控。
- 反复注入长提示词会带来 token 成本与漂移风险（越长越不稳）。

因此 vNext 的原则是：**不要追求“常驻提示词”**，而要追求：
- 规则由内核执行（硬约束），而不是靠模型记忆（软约束）。
- 规程可检索、可按需加载、可版本化（skill）。

## 2) Skills 与 MCP：各自负责什么（避免角色错位）

### 2.1 Skills（Agent Skills）负责“怎么做”（规程/范式），不负责“授权/执行真相”

skills 的定位：把长规程产品化为一个可发现的目录（`SKILL.md` + 可选 scripts/resources），按需加载，减少重复 prompting。

多 runtime 现状：
- Claude Code：支持 `~/.claude/skills/`（个人）与项目内 `.claude/skills/`（团队共享）。  
  https://code.claude.com/docs/en/skills
- OpenCode：支持 `.opencode/skill/` 与 `~/.opencode/skill/`，并兼容 `.claude/skills/`。  
  https://opencode.ai/docs/skills/
- Codex：支持 `.codex/skills` 与 `~/.codex/skills` 等，并强调 progressive disclosure。  
  https://developers.openai.com/codex/skills

但无论哪家实现，skills 的本质仍然是“指令/规程”，不是硬权限与权威状态机。

### 2.2 MCP/CLI/Daemon 负责“能做什么 + 做了什么”（权威执行 + 权威记录）

`ccccd`（以及其工具面：MCP/CLI/Web）必须成为“规则的执行者”：
- 结构性动作的权限控制（RBAC）：例如“管理 group/actors/scopes/runners 只允许 foreman（或 user）”。
- 统一写入 ledger（事实流），保证可恢复、可审计。
- 统一投递消息/通知、维护 actor 生命周期。

**结论**：skills = playbook（软）；`ccccd` = law（硬）。

## 3) 隐藏问题：同一 workspace 启动多个 Claude Code actor 会共享 skills 目录

问题复述：同一台机器、同一项目根目录下启动两个 Claude Code（foreman + peer），默认会共享：
- `~/.claude/skills/`（个人 scope）
- `.claude/skills/`（项目 scope）

这会导致“角色规程看起来混在一起”，用户担心会误导。

### 3.1 vNext 的默认解法：共享 skills，但“角色参数化 + 内核 RBAC”

我们**不**用“每角色一套 skills 目录/每 actor 隔离 HOME”作为默认方案，因为那会显著提高配置复杂度并引入状态分裂。

默认方案（推荐、低摩擦、可长期维护）：

1) 只提供一个权威 skill：`cccc-ops`  
   在同一个 `SKILL.md` 内按角色分区：
   - Foreman Playbook
   - Peer Playbook（适用于所有 peers）

2) 角色来源必须稳定可得（不靠模型猜）：
   - actor 启动时由 CCCC 注入一条极短 bootstrap（或环境变量 `CCCC_ROLE` / `CCCC_ACTOR_ID`）。
   - skill 的第一条规则：先确认自己的 role，再只执行该 role 的分区。

3) 任何越权动作由内核拒绝：
   - 即使 peer 误读/误触发了别的角色分区，调用动作时也会被 `ccccd` 拒绝。
   - 这才是“经得起考验”的底线：**提示词错了也不出大事**。

### 3.2 可选但不默认：为每个 actor 隔离 `HOME`（从根上隔离 `~/.claude/*`）

在极端情况下（例如企业环境/强隔离需求），可以让 CCCC 启动 actor 时为其设置不同 `HOME`，从而隔离 `~/.claude/skills` 与配置缓存。

但该方案成本高：
- Claude 的 settings/plugins/MCP 配置也会被拆分，维护复杂度上升。
- 调试与用户心智成本更高。

因此它是“可选增强”，不作为 vNext 默认。

## 4) 文案与机制如何避免漂移（Single Source of Truth）

为了避免“system prompt / skills / MCP descriptions 三套文案”：

- **长规程只写一次**：写在 `cccc-ops` skill（或其引用的同一份文件）里。
- **Bootstrap 只写指针**：启动时短消息只告诉“规程入口 + 角色边界 + 核心命令入口”。
- **工具描述只写 API 事实**：MCP tools/CLI help 只描述“做什么/参数/返回”，不承载 HOWTO。

## 5) 这套方案对 CCCC vNext 的直接要求（实现清单）

vNext 要真正落地上述方案，至少需要：

1) **RBAC 落在内核**：`ccccd` 对“结构性动作”做硬校验（防重复/防冲突）。
2) **统一 role 标识**：actor 的 role/权限在 group 元数据中是权威字段，并且会随启动注入给 actor。
3) **一个内置 skill 模板**：`cccc-ops` 由 CCCC 自带/生成（避免用户手写安装步骤），并尽可能自动部署到常见 runtimes 的技能目录。
