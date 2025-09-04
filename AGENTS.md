CCCC Agents Guide (S‑Pair v1.1)

Purpose
- Build an orchestrator that enables two equal peers (e.g., Claude Code and Codex CLI) to co‑drive software delivery via debate and verifiable evidence on a single branch. Minimize human intervention to high‑impact decisions through Request‑For‑Decision (RFD), while maintaining a strict evidence‑first governance.

Roles & Responsibilities
- Peer A (Claude‑like): product shaping, architecture, security review; can code.
- Peer B (Codex‑like): implementation, testing, performance; can plan.
- Leader (rotates, low frequency): enforces protocol (steelman, counters quota), not a final arbiter.
- User (decision maker): sets vision, gates autonomy, resolves RFDs.

Core Principles
- Evidence‑first: only EVIDENCE (patch/test/log/bench) changes system state; chat alone never does.
- Single‑branch, small steps: commit queue + soft path locks; per patch ≤ 150 changed lines.
- Counterbalance: enforce COUNTER quota each phase (≥3, with ≥1 strong opposition) and steelman the opposing argument before proceeding.
- Gates: irreversible changes (arch/schema/public API) and releases require dual‑sign from A+B.
- Safety: minimal privilege, sensitive data only referenced, not inlined.

Message Contract (strict)
Every agent message has three parts. <TO_PEER> MUST be valid YAML. <TO_USER> is a concise human‑readable status.

Example <TO_USER>
```
<TO_USER>
- Outcome: Implemented queue preflight; 2 patches passed fast tests.
- Evidence: commit:abc123, LOG:run45#L12-40
- Risks: lint slow on CI; proposed caching.
- Decision needed: None.
</TO_USER>
```

Example <TO_PEER> (YAML)
```
<TO_PEER>
type: CLAIM  # CLAIM | COUNTER | EVIDENCE
intent: implement  # discovery|shape|arch|ux|implement|review|test|security|perf|release|ops|rfd
tasks:
  - desc: "Add commit queue with git apply --check preflight"
    constraints: { allowed_paths: ["orchestrator/**",".cccc/**","config/**"], max_diff_lines: 150 }
    acceptance: ["A1: queue serializes", "A2: preflight short-circuits on failure", "A3: path locks prevent overlap"]
refs: ["SPEC:PRD#3", "TEST:queue#smoke"]
</TO_PEER>
```

System Notes schema
```
<SYSTEM_NOTES>
agent: peerA|peerB
role: leader|challenger
confidence: 0.0-1.0
needs_decision: false|true
budget: { tokens_used: N, patches: M }
phase: discovery|shape|arch|impl|quality|release|growth
</SYSTEM_NOTES>
```

Evidence Types & How to Verify
- Patch evidence: git patch applied and merged by queue; reference short commit hash.
- Test evidence: named test file/case + command + log slice reference (e.g., LOG:pytest#L20-42).
- Benchmark/log evidence: tool name, scenario, and stable metrics with source log reference.
- Rule: every “done” claim links at least one evidence reference; the ledger rejects state changes without references.

Preflight Pipeline (configurable in policies.yaml)
1) git apply --check against a clean temp worktree
2) Lint/format (language‑aware if available; else skip)
3) Fast tests (project‑specific quick suite)
4) On any failure: do not touch working tree; return minimal fix request

Soft Locks & Paths
- Locks are patterns over paths (e.g., src/api/**); queue enforces one owner at a time with TTL.
- Conflicts convert into COUNTER with reproduction notes or are queued.

Confidence Calibration (rule‑based v0)
- 0.3: proposal without runnable evidence
- 0.6: local tests/logs for the touched scope pass
- 0.8: end‑to‑end fast tests green; peer reviewed
- 1.0: dual‑sign or user RFD decision recorded

RFD (Request‑For‑Decision)
- Trigger: A and B confidence < 0.6 for 2 consecutive rounds on the same topic, or high‑impact irreversible change.
- Card fields: alternatives, impact, rollback, default, time limit. Decision recorded in ledger; default executes on timeout.

Ledger & Observability
- Storage: start with JSONL (easy to replay), later migrate to SQLite.
- Event fields: id, ts, phase, kind (CLAIM|COUNTER|EVIDENCE|RFD|DECISION), from→to, confidence, refs, payload digest, gate checks.
- Rule: only EVIDENCE events transition codebase state; others annotate context.
- Minimal dashboards: success rate, preflight time, failure reasons, gate hits.

Security & Safety
- Do not emit secrets or .env contents; reference file paths/line ranges instead.
- Redact sensitive logs; keep tokens outside logs.
- Run with minimal filesystem/network permissions required.

Milestones (from PRD)
- M0: PoC loop — fuzzy goal → A/B handoff → minimal patch → preflight → ledger.
- M1: Commit queue + soft locks + evidence ledger + rule‑based confidence.
- M2: Telegram bridge for <TO_USER>/<TO_PEER>, inline RFD, write‑back to ledger (implemented; see “M2 Status — Telegram Bridge”).
- M3: Docshot incremental context; deviation detection; auto correction proposals.
- M4: Security/dep scan, secrets detection, perf gates, plugin third roles.

Repository Pointers
- PRD: docs/PRD-and-Roadmap.md
- Orchestrator runtime (PoC): .cccc/orchestrator_tmux.py (invoked by cccc.py)
- Runner: cccc.py looks for CCCC_HOME (defaults to .cccc)
- Note: top‑level README quickstart mentions orchestrator/orchestrator_poc.py which is not present; prefer using cccc.py.

Orchestrator Domain (.cccc) Boundaries
- Domain: `.cccc/**` is the orchestrator domain, not business code or assets.
- Allowed writes: mailbox (`.cccc/mailbox/**`), shared workpad (`.cccc/work/**`), logs (`.cccc/logs/**`), and ephemeral state/locks (`.cccc/state/**`). These are non‑authoritative and may be rotated/cleaned.
- Restricted changes: orchestrator code/config/policies under `.cccc/**` require an RFD and dual‑sign; do not modify casually.
- Non‑mix rule: business changes must land via `patch.diff` into business paths outside `.cccc/**`. Do not treat `.cccc/**` artifacts as business deliverables.
- Persistence: promote any long‑term evidence from `.cccc/work/**` into `docs/evidence/**` or `tests/fixtures/**` via patch, including provenance (tool, source, hash/size).
- Hygiene: `.cccc/work/**` is git‑ignored by default; never store secrets; prefer stable log references `LOG:tool#Lx-Ly` or file slices like `.cccc/work/logs/*.txt#Lx-Ly`.

Working Agreement (Agents)
- Always steelman major COUNTERs and seek explicit confirmation before dismissal.
- Enforce COUNTER quota per phase; include at least one “strong opposition” with a concrete risk/alternative and reproduction.
- Keep diffs scoped and ≤ 150 lines. For larger refactors, file an RFD to request an exception with stricter preflight.
- Reference facts: every claim/decision ties to commit/test/log IDs.
- Prefer incremental, verifiable steps to speculative architecture changes.

Message Templates (copy‑ready)
```
<TO_USER>
- Goal: <one‑line outcome>
- Progress: <evidence refs>
- Risks/Blocks: <top risks>
- Decision needed: <None|RFD:id>
</TO_USER>

<TO_PEER>
type: COUNTER
intent: review
tasks:
  - desc: "Preflight skips lint on CI when ruff present"
    constraints: { allowed_paths: [".github/**","orchestrator/**"], max_diff_lines: 80 }
    acceptance: ["A1: CI lint runs via ruff", "A2: local fallback works"]
refs: ["TEST:lint#smoke"]
</TO_PEER>

<SYSTEM_NOTES>
agent: peerB
role: challenger
confidence: 0.62
needs_decision: false
budget: { tokens_used: 0, patches: 0 }
phase: impl
</SYSTEM_NOTES>
```

Pending Decisions (to align with user)
- Orchestrator language and runtime for M1 (suggest Python; current PoC under .cccc).
- Default preflight commands (lint/test), and how to auto‑detect vs explicit policies.yaml.
- Agent adapters: first‑class targets (claude‑code, codex‑cli) and protocol abstraction to swap models.
- Exception path for >150‑line diffs (RFD + stricter gates) — accept?
- Confidence policy: stick to rule‑based v0 with optional agent self‑report weighting?
- Ledger backend: JSONL now, migrate to SQLite at M1.5?

Quickstart
- Run `python cccc.py` in the repo root. An onboarding wizard can help configure Telegram (token via env, allowlist discovery). Autostart is enabled when configured.

Project Status (Now)
- Prompts: `PEERA.md`/`PEERB.md` aligned (v2). Persona cue added; PCR+Hook guidance mapped to CLAIM/COUNTER/EVIDENCE; `.cccc/work/**` shared workspace rules embedded. Startup system prompt aligned via `prompt_weaver.py`.
- Boundaries: `.cccc/**` is orchestrator domain. Allowed writes: `.cccc/mailbox/**`, `.cccc/work/**`, `.cccc/logs/**`, `.cccc/state/**` (non‑authoritative). Orchestrator code/config/policies remain guarded.
- NUDGE stability: simplified to “paste → single Enter”. No pre‑Enter poke; extra Enters disabled by default. NUDGE text enforces a closed loop: read the oldest inbox file → act → output → move that file to `processed/` → next.
- Inbox numbering: per‑peer fcntl lock + counter under `.cccc/state/` generates monotonic 6‑digit sequences (eliminates duplicate inbox filenames under concurrency).
- Telegram bridge: robust inbound/outbound with explicit routing, reply‑routing, file exchange, startup noise suppression, and minimal commands (details below).
- Periodic self‑check: configurable cadence and text. After every N user/system handoffs (default 20), system asks both peers short self‑inspection questions; emits `kind:self-check` in ledger.
- Hygiene: `.gitignore` ignores `.cccc/work/**`; evidence “promotion” path documented.

Configs (added/changed)
- `.cccc/settings/cli_profiles.yaml`
  - `delivery.self_check_every_handoffs: 20` — 0 disables.
  - `delivery.self_check_text: |` — multiline self‑check prompt text.
  - `peerB.post_paste_keys: ["Enter"]` — default single Enter submit.
- `.cccc/prompt_weaver.py` — startup system prompt includes persona cue, PCR+Hook hint + exemptions, `.cccc` allowed writes, and Telegram file exchange guidance (outbound photos/files folders, `.caption.txt`, `.sendas`).

Self‑Check (lightweight governance)
- Trigger: counts non‑NUDGE handoffs from User/System; every Nth triggers for both peers.
- Text: sourced from `delivery.self_check_text`; keep concise (≤1 line per answer).
- Ledger: emits `kind: self-check` entry.
- Goal: prevent drift/looping; reinforce persona and first‑principles without heavy rules.

NUDGE Reliability (updated)
- Orchestrator NUDGE: paste, then a single Enter; no pre‑Enter “poke”, no extra Enters by default (configurable per‑peer if needed).
- NUDGE text: closed loop of processing inbox files in order and moving processed files to `processed/`.
- Bridge mode: adapter writes `inbox.md`; orchestrator avoids duplicate submits; CLI profiles keep minimal `send_keys=["Enter"]`.

M2 Status — Telegram Bridge (implemented)
- Mode: long‑polling `getUpdates` with singleton lock; logs to `.cccc/state/bridge-telegram.log`; optional dry‑run（文件驱动的模拟）。
- One bot, explicit routing: require `a:`/`b:`/`both:` prefix or `/a` `/b` `/both` commands in groups; mention form `@Bot a:` supported；可选 `require_mention`。
- Reply‑routing: reply to a message with only `/a|/b|/both` routes the quoted text and attachments to the selected peer(s)。
- Inbound text: auto‑wrap to `<FROM_USER>…</FROM_USER>` when no known tags；插入 `[MID: …]` 于开标签后。按序写入 `.cccc/mailbox/<peer>/inbox/000123.<MID>.txt`（同时镜像到 `inbox.md`）。
- Inbound files: 保存至 `.cccc/work/upload/inbound/<chat>/<date>/<MID>__name`，附 `.meta.json`（mime/bytes/sha256/caption/ts）。Inbox 引用包含文件路径+元信息。
- Outbound messages: 监听 `to_user.md`（默认仅 PeerA，可由 `outbound.watch_to_user_peers` 配置）与 `to_peer.md`（当启用），发送去重/节流后的摘要。Peer↔Peer 摘要默认开启；可用 `/showpeers on|off` 切换。
- Outbound files: 从 `.cccc/work/upload/outbound/<peer>/{photos|files}/` 发送；支持 `.caption.txt`（<=900 chars）与 `.sendas`（`photo|document`）旁注；自动猜测 MIME，图片默认 `sendPhoto`。
- RFD cards: 监控 ledger 的 `kind:rfd`，下发带内联按钮的卡片（Approve/Reject/Ask More）；决策落账为 `kind:decision`。
- Startup noise suppression: `.cccc/state/outbound_seen.json` 持久化基线，避免重发历史；首次启动推进 offset 跳过 Telegram 历史。支持 `outbound.reset_on_start: baseline|archive|clear`。
- Security: 通过 `.cccc/settings/telegram.yaml` 配置 allowlist；支持脱敏与大小上限；EXIF 可配置剥离；网络请求最小化并记录日志。

Telegram Commands（最小集）
- `/help`：用法与路由提示。
- `/whoami`：返回 `chat_id` 以便加入 allowlist。
- `/subscribe` `/unsubscribe`：自助订阅/取消（当 `autoregister: open`）。
- `/showpeers on|off`：切换 Peer↔Peer 摘要（全局运行时，默认 ON）。
- `/status` `/queue` `/locks`：查看 `.cccc/state/` 的基本快照。
- `/files [in|out] [N]`、`/file N`：列最近文件并查看详情（path/size/MIME/SHA256/caption）。

Process Management（bridge）
- `cccc.py` 提供引导式向导，支持通过环境变量输入 Token、allowlist 发现与可选 `chat_id` 直填。
- 多仓隔离：仅在确认为“本仓库的桥接进程”后才会终止旧进程（校验 `/proc/<pid>/cmdline`）。
- 写入 PID 并在退出时优雅清理（SIGTERM→wait→SIGKILL）。在配置或 `dry_run: true` 时自动启动。

Defaults（开箱即用）
- `autostart: true`，`dry_run: false`，`show_peer_messages: true`。
- Routing：`require_explicit: true`，`allow_prefix: true`，`require_mention: false`（群组可选）。
- Files：`files.enabled: true`，`max_mb: 16`，`inbound_dir: .cccc/work/upload/inbound`，`outbound_dir: .cccc/work/upload/outbound`，`strip_exif: true`。
- Outbound：`outbound.watch_to_user_peers: [peerA]`，`outbound.reset_on_start: baseline`。

Next Steps（toward a polished OSS product）
- Per‑chat showpeers：从全局切换为“按 chat 持久化”的细粒度控制。
- 文件提级与校验：`/promote` 将入站文件从 `.cccc/work/upload/inbound/**` 提升到 `docs/evidence/**`（通过 patch）；`/check` 运行 Evidence Runner 校验（见 `Evidence-Card-Spec.md`）。
- 远程维护：新增 `/outbound reset baseline|archive|clear`，便于无 SSH 情况下重置出站基线。
- 媒体增强：统一回复路由 UX，并支持 Telegram 图集（`sendMediaGroup`）以更好地承载多图/多文件。
- 上传健壮性：可选引入 `requests` 简化 multipart、改进超时/重试；在安全前提下提升 `max_mb`；分片发送更大文件。
- 可观测性：提供轻量 `/dashboard` 链接；ledger 从 JSONL 迁移到 SQLite，提升可查询与持久化能力。
- 安全加固：默认剥离 EXIF，可选恶意文件扫描（如 clamscan）；对入/出站路径实施严格 allowlist；日志持续脱敏。
- 体验打磨：精简 `/help` 文案、RFD 卡片包含关键上下文；在 README 提供“一键上手（bridge + CLI + evidence‑first）”快速指引。
