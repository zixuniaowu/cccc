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
- M2: Telegram bridge for <TO_USER>/<TO_PEER>, inline RFD, write‑back to ledger.
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
- Local run suggestion: `python cccc.py` after setting environment if needed; see README.md for details.

Project Status (Now)
- Prompts: `PEERA.md`/`PEERB.md` fused with v2. Add Persona (humanized co‑leader stance), PCR+Hook as soft‑required, mapping to CLAIM/COUNTER/EVIDENCE, and `.cccc/work/**` shared workspace rules. Startup system prompt aligned.
- Boundaries: `.cccc/**` clarified as orchestrator domain. Allowed writes: `.cccc/mailbox/**`, `.cccc/work/**`, `.cccc/logs/**`, `.cccc/state/**` (non‑authoritative). Orchestrator code/config/policies remain guarded.
- NUDGE robustness: bridge adapter and tmux path hardened to reliably submit after paste (CR/LF/Ctrl‑J variants + pane poke). Configurable `post_paste_keys` per peer.
- Periodic self‑check: new configurable cadence and text. After every N user/system handoffs (default 20), system asks both peers 5 short self‑inspection questions before proceeding.
- Ignore rules: `.gitignore` ignores `.cccc/work/**` by default; evidence “promotion” documented.

Configs (added/changed)
- `.cccc/settings/cli_profiles.yaml`
  - `delivery.self_check_every_handoffs: 20` — 0 disables.
  - `delivery.self_check_text: |` — multiline self‑check prompt text.
  - `peerB.post_paste_keys: ["Enter", "C-m"]` — more robust submit after paste.
- `.cccc/prompt_weaver.py` — startup system prompt now mentions persona cue, PCR+Hook hint + exemptions, and `.cccc` allowed writes.

Self‑Check (lightweight governance)
- Trigger: counts non‑NUDGE handoffs from User/System; every Nth triggers for both peers.
- Text: sourced from `delivery.self_check_text`; keep concise (≤1 line per answer).
- Ledger: emits `kind: self-check` entry.
- Goal: prevent drift/looping; reinforce persona and first‑principles without heavy rules.

NUDGE Reliability (Codex CLI update adaptation)
- Bridge adapter (`.cccc/adapters/bridge.py`): after writing payload, sends `sendline("")`, `Ctrl‑M ×2`, raw `\r ×2`, raw `\n`, `Ctrl‑J`, with short delays.
- Orchestrator nudge path: after writing inbox in bridge mode, sends a best‑effort `Enter` to the pane as a poke.
- Profiles: per‑peer `post_paste_keys` customizable.

M2 Plan — Telegram Bridge (MVP)
- Goal: chat→mailbox→peers with evidence‑first flow; minimal, reversible.
- Process: separate bridge process reads Telegram (long polling); writes `<FROM_USER>…</FROM_USER>` with `[MID: …]` into `.cccc/mailbox/peerA|peerB/inbox.md` (default broadcast; support `a:`/`b:`/`both:` prefixes). Tails `peerA/to_user.md` and posts concise updates back.
- Safety: token via env var; chat allowlist in `.cccc/settings/telegram.yaml`; redact and cap message size; no diffs in chat (point to repo files instead).
- Reliability: file‑based handoff only; retries/backoff; logs to `.cccc/state/bridge-telegram.log` and annotates ledger (`from=user-telegram`).
- Acceptance (P0):
  - A1: Inbound chat reaches both peers and includes `[MID]` ack flow.
  - A2: Outbound to_user summaries appear in chat (debounced, truncated).
  - A3: Ledger entries recorded for inbound/outbound.
  - A4: No secrets leaked; only allowlisted chats accepted.

Open Decisions (bridge)
- Broadcast default: always both, or remember last addressed peer?
- Outbound scope: only `to_user` vs include patch/test short lines.
- Formatting: plain text vs Markdown; per‑chat toggles?
- Placement: launch bridge as third tmux pane vs separate process/service.

Next Steps (proposed)
- Draft `./.cccc/adapters/telegram_bridge.py` skeleton + `settings/telegram.yaml` (token env var name, chat allowlist, post options), docs page.
- Dry‑run with mock transport (no network) to validate mailbox integration; then gate networked run behind explicit token.
