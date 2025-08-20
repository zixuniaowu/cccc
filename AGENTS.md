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
