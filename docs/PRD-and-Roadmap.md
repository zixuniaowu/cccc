CCCC：双 AI 结伴编程编排器（S‑Pair v1.1）

愿景：让两位对等 AI（例如 Claude Code 与 Codex CLI）在一个群聊/对话界面中，以对等辩论 + 证据优先 + 单分支快跑的方式，从模糊愿景自驱推进到可发布产品。用户只在关键节点裁决/授权，其余由 AI 自治推进。

1. 目标与非目标
1.1 目标

端到端自治：覆盖从 Discovery（发现/竞品/用户画像）→ Shaping（PRD/路线图）→ 架构/UX → 实现/测试 → 安全/性能 → 发布/回滚 → 增长/运维 全生命周期。

对等纠偏：两位 AI 同级互质疑（COUNTER 配额 + 钢人化复述），用**可验证证据（EVIDENCE）**达成共识。

对话可视化：用户在一个界面看到 <TO_USER> 摘要；AI 间交接 <TO_PEER> 自动路由，必要时折叠展示。

单分支快跑：不引入复杂分支/PR；用提交队列 + 软锁保障秩序；每次补丁 ≤150 行。

轻介入：遇到高影响/低置信度分歧时自动发起 RFD（请示—裁决），用户一键决策。

1.2 非目标

不打造 IDE；不取代 CI/CD；不暴露冗长“思维链”。

不承诺“任何复杂系统两天完成”；我们承诺高效且可验证地推进。

2. 角色、专长与治理

Peer A（如：Claude）：稳定专长 = 产品/架构/安全审查（也可编码）。

Peer B（如：Codex）：稳定专长 = 实现/测试/性能（也可规划）。

队长（Leader）：由用户指定，只在阶段切换/僵局时低频轮换；执行权≠裁决权。

用户（决策者）：定义愿景；处理 RFD；设定门控与自治等级。

3. S‑Pair 协议（关键规则）

话语类型：

CLAIM（主张）：方案/设计/路线；

COUNTER（反证）：风险/替代方案/反例；

EVIDENCE（证据）：补丁/测试/日志/基准结果（唯一能改变系统状态）。

钢人化：对重大 COUNTER，队长必须先准确复述对方最强论据并获得确认。

COUNTER 配额：每阶段挑战者至少 3 条 COUNTER（含 1 条重大反对）。

RFD 触发：双方置信度<0.6 且分歧持续 2 轮 → 自动生成 RFD（备选/影响/回滚/默认项）。

双签门（Dual‑Sign Gates）：不可逆变更（架构/数据迁移/公开 API）与发布必须经 A+B 同意。

单分支 + 提交队列：补丁入列→预检（git apply、lint、快测）→通过才提交；路径软锁避免互踩。

4. 信息结构与消息协议

每条 AI 消息必须分三段（对话可视化 + 路由）：

<TO_USER>
# 面向用户：目标/进展/风险/需裁决（RFD，如有）。
# 必须附事实引用（commit/test/log/bench 的链接或短哈希）。
</TO_USER>

<TO_PEER>
type: CLAIM|COUNTER|EVIDENCE
intent: discovery|shape|arch|ux|implement|review|test|security|perf|release|ops|rfd
tasks:
  - desc: "补齐 tests/import_csv.spec.ts 用例 #1~#3"
    constraints: { allowed_paths: ["src/**","tests/**"], max_diff_lines: 150 }
    acceptance: ["A1","A2","A3"]
refs: ["SPEC#2.1","TEST:import_csv#case3","commit:abc123","LOG:run45#L12-40"]
</TO_PEER>

<SYSTEM_NOTES>
agent: peerA|peerB
role: leader|challenger
confidence: 0.0-1.0
needs_decision: false|true
budget: { tokens_used: N, patches: M }
phase: discovery|shape|arch|impl|quality|release|growth
</SYSTEM_NOTES>

5. 生命周期覆盖（阶段→必交工件→门控）
阶段	必交工件（Examples）	门控（通过条件）
Discovery	PROBLEM.md、PERSONAS.md、COMPETITORS.md、RISKS.md	双方同意问题/用户画像；关键风险列举齐全
Shaping	PRD.md、Roadmap.md、Milestones.md、NonGoals.md	PRD 锁定（双签）；范围/里程碑一致
Arch/UX	ARCH.md、API.md、SCHEMA.*、原型稿/线框图	架构/数据模型落锤（双签）
Implementation	tests/*、src/*、CI config	快测绿灯；每补丁 ≤150 行
Quality	安全（SAST、依赖扫描）、性能（基准曲线）、可观测（埋点/告警）	阈值达标；回归不红
Release	CHANGELOG.md、release.sh、回滚方案	版本发布双签门
Growth/Ops	Events.md、Dashboards.md、Runbook.md	告警/仪表上线；事件字典冻结
6. 功能需求（MVP → 进阶）
6.1 MVP（里程碑 M1）

编排器：解析 <TO_USER>/<TO_PEER>/<SYSTEM_NOTES>；自动把 A 的 <TO_PEER> 投递给 B，反之亦然。

提交队列 + 软锁：统一 diff 预检（git apply、lint、快测）；路径白名单与单补丁行数上限。

RFD：自动生成裁决卡（默认项/回滚路径）；用户选择后写入**主张账本（ledger）**并广播。

主张账本：CLAIM/COUNTER/EVIDENCE 的记录与闭环校验；每条“完成”声明必须附事实锚点。

配置：roles.yaml（稳定专长、队长）、policies.yaml（门控/阈值/自治等级）。

6.2 M2（对话可视化与自治深化）

Telegram 群聊桥：展示 <TO_USER>、折叠 <TO_PEER>；系统卡片（补丁/测试/CI）；RFD 内联按钮。

Docshot / 增量上下文：自动抽取改动相关片段，降低 token；周期性纪要合并降噪。

偏航探测：连续失败/低置信度/无证据争执 → 自动纠偏提案或 RFD。

6.3 M3+（质量、安全、性能、扩展）

安全/合规：SAST、依赖漏洞扫描、Secrets 检测、日志脱敏。

性能：基准场景与回归门。

第三角色可插拔：Security Reviewer、Perf Coach。

多项目/多群：本地适配层（agentd）集中化会话管理；profiles.yaml 路由。

7. 非功能需求

可靠性：补丁预检失败不会污染工作树；失败短路回送最小修复请求。

可观测：事件日志/ledger 可回放；RFD/门控决策可追溯。

安全：最小权限运行；敏感日志仅以引用展示；令牌/密钥不泄露。

性能：单补丁预检+快测 < 30s（按项目规模调参）。

可移植：可本地运行（离线）或接入 CI。

8. 成功度量（KPI）

交付用时（Lead time）相对单 AI：0.55–0.75；

回归缺陷密度：0.60–0.80；

返工回合数：0.50–0.70；

用户介入次数/任务：≤3；

Token / 有效 LOC：0.80–0.95。

9. 主要风险与对策

单分支互踩 → 提交队列串行 + 路径软锁。

聊天脱离事实 → 强制事实引用；“事实优先”红线。

过度自治走偏 → 置信度门控 + 两轮分歧自动 RFD。

成本走高 → Docshot + 纪要合并 + <TO_PEER> 仅传增量。

安全泄露 → 脱敏器、敏感路径只发引用。

10. 部署与会话管理（回答“多实例/多会话”）

默认：建议每个仓库一个编排器进程，编排器自己拉起两位代理子进程（最稳妥）。

可选：若 CLI 支持会话 ID，使用 profiles.yaml 指定 --session/--attach 来“附着”。

高级：agentd 守护进程持久化会话，按 project_id 路由群聊消息与补丁。

config/profiles.yaml 示例（支持 attach 的 CLI 时）

projects:
  acme-crm:
    workdir: /work/acme-crm
    agents:
      peerA:
        cmd: "claude-code chat --system - --session acmeA"
      peerB:
        cmd: "codex chat --system - --session acmeB"
  acme-ops:
    workdir: /work/acme-ops
    agents:
      peerA:
        cmd: "claude-code chat --system - --session opsA"
      peerB:
        cmd: "codex chat --system - --session opsB"


若 CLI 不支持会话 ID，就走“每仓库一份编排器 + 子进程持有”的模式；这在功能与隔离上是等价且更可靠的。

11. 路线演进（Milestones & 验收）
M0：仓库搭建（当天）

放入本文档、roles.yaml、policies.yaml、两位 AI 的系统提示、PoC。

验收：在本地跑通一次“模糊愿景 → A/B 交接 → 最小补丁 → 预检/快测”。

M1：提交队列 + 软锁（~3 天）

入列/预检/提交 + 失败短路；路径白名单。

验收：3 次补丁中至少 2 次一次过；无互踩。

M2：Telegram 群聊（~1–2 周）

展示 <TO_USER>、折叠 <TO_PEER>、系统卡片；RFD 内联按钮；群内裁决写回 ledger。

验收：全程在 Telegram 旁观/裁决完成一个中等任务。

M3：Docshot & 偏航探测（~2 周）

增量上下文、纪要合并、低置信度/失败轮数触发纠偏。

验收：两次偏航自动被提案纠正。

M4：质量与扩展（~3–4 周）

SAST/依赖扫描/Secrets；基准门；第三角色插件。

验收：一次安全/性能回归被门控阻断并修复。

12. 开源治理

License：Apache；

目录分层：/orchestrator /adapters /docs /prompts /config /examples；

贡献流程：RFC → Issue → Good‑first‑issue → 里程碑验收。
