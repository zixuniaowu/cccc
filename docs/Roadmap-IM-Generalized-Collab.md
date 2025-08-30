# CCCC 通用化协作 + IM 接入 演进路线（v0.1）

目标：把 CCCC 从“项目开发协作”扩展为“证据优先的双 AI 通用协作层”，以 Telegram（后续 Slack/企业微信/飞书）作为人机协作入口，但坚持聊天不改状态、证据才改状态。

—

一、愿景与核心不变量
- 证据优先：只有 Patch/Test/Log/Bench/Artifact+Check 这类“可验证证据”改变系统状态；聊天仅产生意图/决策与协调。
- 小步可回滚：单分支、提交队列、软锁、每步≤150行或等效粒度（非代码领域按章节/段落/任务槽位映射）。
- 双人制衡：A/B 对等，强制 COUNTER 配额与 steelman，避免单边乐观与幻觉。
- 决策治理：高影响动作以 RFD 卡片化，双签或超时默认；所有事件入账可回放（JSONL→后续 SQLite）。
- 通道无关：IM 仅是治理/观察入口；状态变更仍由本地编排与证据门控制。

二、能力地图（面向通用场景）
- 通用工件与检查（Artifact+Check）：任何领域的“成果”均可表达为工件（文件/链接/片段）与检查（命令/脚本/规则）。
- 证据卡片（可选表述）：CLAIM/COUNTER/EVIDENCE 可挂载 artifacts[] 与 checks[]；编排器只负责执行检查、收集日志、写账。
- NUDGE 闭环：读取→执行→输出→ACK/归档→下一个；文件移动或 ack: <seq> 任一达成闭环。
- RFD 流：备选/影响/回滚/默认/时限；IM 里简明审批，记录入账。

三、阶段路线与验收

M0 最小内核梳理与闭环强化（本地）
- 目标：明确“少而硬”的内核；不预设行业“证据类型注册表”，仅提供通用 Artifact+Check 能力与轻量证据卡片表述。
- 交付：
  - NUDGE 文案闭环升级（已完成）：指示读取→执行→输出→ACK/归档→下一个；若无法移动文件允许 ack: <seq>。
  - Inbox 序号并发修复（已完成）：per-peer 锁+计数器，消除重号。
  - 通用证据卡片（文档规范）：事件字段（artifacts、checks、logs、verdict、metrics）与使用示例。
- 验收：
  - 本地干跑可执行 checks（非领域特定），日志可引用（LOG:tool#Lx-Ly）。
  - 闭环不再出现“读了却无输出”的超时（新增 no-output-after-read 警示比率≤5%）。

M1 Telegram 桥接 MVP（安全、低打扰）
- 目标：群聊→mailbox（inbox.md）输入；to_user 摘要→群聊输出；严控内容为“短摘要+证据引用”。
- 交付：
  - 进程式桥接（长轮询）：令牌 via 环境，chat allowlist，消息大小上限，脱敏与日志落地到 .cccc/state/。
  - 入站：用户消息写入 `.cccc/mailbox/peerA|peerB/inbox.md`，带 [MID]；支持 a:/b:/both: 前缀定向。
  - 出站：监听 `to_user.md`（简报四行：Outcome/Refs/Risks/Decision-needed），时间窗合并去重，引用 repo 路径/LOG 片段。
  - Ledger：入站/出站事件打点，来源标识 `from=user-telegram`。
- 验收：
  - A1 入站稳定（丢失率≈0；重复率≈0，经 MID 去重）。
  - A2 出站节流与合并（每 5 分钟≤N 条，且不贴大 diff）。
  - A3 安全：仅 allowlist 群/人；无秘钥泄漏；日志不含 token。
  - 指标：RFD P50 决策时延可见；Chat→Evidence 转化率开始统计。

M1.5 通用 Evidence Runner v0
- 目标：执行证据卡片中的 checks（命令/脚本/规则），产生 verdict/metrics 与日志引用；仍不内建行业注册表。
- 交付：
  - 轻量运行器：串行执行 checks（可声明超时/重试），收集 stdout/stderr，产生日志文件并生成引用片段。
  - Gate：当 checks fail→自动生成 COUNTER 提示补救或请求 RFD；pass→允许合入/晋升（由队列与锁保证）。
- 验收：
  - A1 checks 失败率/恢复时间可观测；错误 Top-N 原因周报可生成。
  - A2 Chat→Evidence 转化率显著>接入前基线。

M2 IM 交互增强与治理（RFD 卡片、状态/锁查询）
- 目标：在 Telegram 中以按钮/命令进行 RFD 审批、状态/队列/锁查询，减少切换成本。
- 交付：
  - RFD 卡片：Approve/Reject/AskMore 按钮；默认/时限；决策写账。
  - 命令：/status /queue /locks /pause /resume /rfd …（返回短摘要+链接）。
  - 线程/话题映射（轻量）：为 RFD/任务提供可链接的消息锚点，减少上下文串扰。
- 验收：
  - A1 RFD 时延（P50/P95）下降；AskMore 留痕与超时默认行为准确。
  - A2 误触低，重复提醒率下降（时间窗合并+抖动）。

M3 多通道与身份映射（Slack/企业微信/飞书）
- 目标：抽象 IM 协议层，复用桥接能力；解决群/用户身份映射与授权一致性。
- 交付：
  - Adapter 抽象：统一入站/出站接口；频道→任务/仓库映射配置。
  - 身份：IM 用户→本地身份映射与授权分级（只读/审批/维护者）。
- 验收：
  - A1 多通道切换零侵入核心；身份/授权一致可验证。

M4 模版晋升与观测（可选，不强制）
- 目标：把高频复用的 checks/对话套路由 A/B 自主发起 RFD 晋升为“建议模板”（文档层），而非强制注册表。
- 交付：
  - docs/templates/**：以文档形式保存“建议检查/脚本/参数”，附来源、适用范围与反例。
  - 观测：/dashboard 短链展示成功率、RFD 时延、重发率、Top-N 失败原因。
- 验收：
  - A1 采用率与退回率可见；模板不使用时系统仍完全可用。

四、体验规范（跨阶段）
- 群内只发四行摘要 + 短引用；长内容入仓库/证据目录。
- 任何“直接执行”请求需回到证据门（Artifact+Check）；危险操作走 RFD 双签。
- NUDGE 闭环可理解且可执行；遇阻只提一个 QUESTION，降低噪音。

五、度量指标（成功与否的尺子）
- RFD 决策时延（P50/P95）。
- Chat→Evidence 转化率（讨论转化为落地证据）。
- 预检通过率与修复回合数；失败 Top-N 原因闭环速度。
- 重发/超时率（NUDGE/桥接）。
- “无输出读完归档”告警比率（no-output-after-read）。

六、风险与缓解
- 控制错觉：聊天不直改状态，所有变更走证据门与队列。
- 噪音：时间窗合并、哈希去重、长文外链；线程/话题锚点。
- 安全：令牌仅环境变量；日志脱敏；仅 allowlist 频道；DLP 与敏感词默认拒绝直发。

七、立即下一步（建议）
- 写通用“证据卡片”的简版规范与示例（不落代码，先文档对齐）。
- 起草 `./.cccc/adapters/telegram_bridge.py` 骨架与 `settings/telegram.yaml`（令牌 env 名、allowlist、节流策略）；实现离线干跑。
- 增加 no-output-after-read 的轻量告警打点（ledger），用于观察 NUDGE 闭环质量。

