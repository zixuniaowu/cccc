# CCCC Pair — Dual‑AI Orchestrator (Evidence‑first) · Agent‑as‑a‑Service (AaaS)

Two best‑in‑class AI CLIs (Claude Code + Codex CLI) co‑drive your work as equal peers. They collaborate, self‑review, and ship small, reversible changes with built‑in governance. You observe and nudge from tmux or IM. CCCC treats agents as long‑lived services — Agent‑as‑a‑Service (AaaS) — that speak the same contract, produce auditable evidence, and integrate with your team’s tools.

Not a chatbot UI. Not an IDE plugin. A production‑minded orchestrator for 24/7, long‑running work.

## Why It’s Different

- Dual‑AI Autonomy: peers continuously plan → build → critique → refine. They don’t wait for prompts; they follow a mailbox contract and change the world only with EVIDENCE (diff/tests/logs/benchmarks).
- Agent‑as‑a‑Service (AaaS): agents are long‑running services with a mailbox contract, not ad‑hoc prompts. They keep rhythm, produce evidence, and integrate with IM (Telegram/Slack/Discord via bridges; Teams outbound planned) without coupling core logic to any single transport.
- IM Collaboration: a 24/7 agent lives in your team chat. High‑signal summaries, one‑tap RFD decisions, explicit routing (a:/b:/both:) so normal conversation remains normal. Files flow both ways with captions and sidecars.
- Builder–Critic Synergy: peers challenge CLAIMs with COUNTERs and converge via verifiable EVIDENCE. In practice this beats single‑model quality on complex, multi‑day tasks.
- tmux Transparency: dual panes (PeerA/PeerB) + compact status panel. You can peek, nudge, or inject text without disrupting flows—and without a GUI.
- Governance at the Core (RFD): protected areas and irreversible changes raise Request‑For‑Decision cards in chat; approvals are written to a ledger and unlock execution.
- Team‑level Efficiency: one always‑on bot concentrates orchestration and approvals for the whole team. You reduce duplicated “per‑seat” sessions and still retain control.

## What You Get

- Evidence‑first loop: tiny diffs/tests/logs; only green changes commit.
- Single‑branch queue: preflight `git apply` → (optional) lint/tests → commit.
- RFD closed loop: generate cards, gate protected paths/large diffs, unlock on decision.
- AaaS integration: explicit routing; `/status`, `/queue`, `/locks`, `/rfd list|show`, `/showpeers on|off`, file send/receive with meta. Bridges mirror events; orchestrator remains transport‑agnostic.
- Ledger: append‑only `.cccc/state/ledger.jsonl` (patch/test/log/decision) for audit.

## Requirements

- Python `>= 3.9`
- tmux (e.g., `brew install tmux` or `sudo apt install tmux`)
- git (preflight/commits)

Supported CLIs (current)
- Peer A: Claude Code CLI (recommended: MAX Plan)
- Peer B: Codex CLI (recommended: PRO Plan)

## Install

- Recommended (pipx)
  - `pipx install cccc-pair`
- Or venv
  - `python3 -m venv v && . v/bin/activate && pip install cccc-pair`

## Quick Start (5 minutes)

1) Initialize in your project repo

```
cccc init
```

This copies the scaffold to `./.cccc/` and appends `/.cccc/**` to `.gitignore` (Ephemeral).

2) Check environment

```
cccc doctor
```

3) (Optional) Enable Telegram

```
cccc token set   # paste your bot token; stored in .cccc/settings/telegram.yaml (gitignored)
```

4) Run orchestrator (tmux UI)

```
cccc run
```

tmux opens: left/right = PeerA/PeerB, bottom‑right = status panel.
Run wizard (interactive TTY) lets you optionally connect a bridge:
- 1) Local only (default)
- 2) Local + Telegram
- 3) Local + Slack
- 4) Local + Discord
You can also manage bridges later via `cccc bridge …` or set `autostart` in `.cccc/settings/*.yaml`.

5) First‑time CLI setup (required)

- Install the two CLIs and make sure the binaries are on PATH:
  - `claude ...` (Claude Code)
  - `codex ...` (Codex CLI)
- Paste system prompts for best collaboration quality:
  - Open `PEERA.md` and copy its full content into a new file `CLAUDE.md` at your repo root (not tracked; `.gitignore` already ignores it). Claude Code CLI should load this as its system prompt.
  - Open `PEERB.md` and copy its full content into a new file `AGENTS.md` at your repo root (not tracked). Codex CLI should load this as its system prompt.
- Move any previous “project state” prompt into `PROJECT.md` at the repo root (recommended). The orchestrator will weave this into the runtime SYSTEM so both peers align on scope and goals.
- Verify or adjust the CLI commands in `.cccc/settings/cli_profiles.yaml`:
  - `commands.peerA: "claude ..."`
  - `commands.peerB: "codex ..."`
  - You can also override at runtime with env vars: `CLAUDE_I_CMD`, `CODEX_I_CMD`.

## First Landing Checklist (Minimal)

Do just these to get a clean, working setup:

1) `cccc init` (creates `./.cccc` and ignores runtime dirs)
2) `cccc doctor` (git/tmux/python)
3) Prepare system prompts (required once per repo)
   - Copy `PEERA.md` → `CLAUDE.md` (root)
   - Copy `PEERB.md` → `AGENTS.md` (root)
   - Put your brief/scope in `PROJECT.md` (root)
4) Optional Telegram (highly recommended)
   - `cccc token set`
   - `cccc run` (bridge autostarts) or `cccc bridge start`
5) Start work: `cccc run` (tmux panes + status panel)

That’s it. You can refine policies later.

## IM Quickstart (Team Hub)

- Group routing: use explicit routes so normal chat stays normal
  - `a: <text>` / `b: <text>` / `both: <text>`
  - or `/a ...` `/b ...` `/both ...` (works with privacy mode)
- Get oriented
  - `/status` project stats; `/queue` handoff queue; `/locks` internal locks
  - `/whoami` shows your chat_id; `/subscribe` (if `autoregister: open`)
  - `/showpeers on|off` toggles Peer↔Peer summaries
- File exchange
  - Outbound (AIs → IM): save under `.cccc/work/upload/outbound/<peer>/{photos,files}/`
  - Optional caption: same‑name `.caption.txt`; force send‑as via `.sendas` (`photo|document`)
  - Sent files are deleted on success; `outbound.reset_on_start: clear` avoids blasting residuals on restart
  - Inbound (IM → AIs): bridge writes `<FROM_USER>` with sidecar meta; peers act on it
- Governance: RFD cards in chat with Approve/Reject; decisions go to the ledger and unlock execution.

## A Typical Session (End‑to‑End, ~3 minutes)

Goal: ship a small, reversible change with dual‑AI collaboration.

1) Explore (short)
- In Telegram (or tmux), route a brief idea to both: `both: Add a section to README about team chat tips`
- PeerA summarizes intent; PeerB asks 1 focused question if needed.

2) Decide (concise CLAIM)
- PeerA writes a CLAIM in `peerA/to_peer.md` with acceptance and constraints (≤150 lines; links to where to edit).
- PeerB COUNTERs if there’s a sharper place or a safer rollout.

3) Build (evidence‑first)
- PeerB produces a tiny unified diff in `peerB/patch.diff` (e.g., add a new README subsection) and a 1–2 line EVIDENCE note (tests OK/lines/paths/MID).
- Orchestrator preflights → applies → (optional) lint/tests → commits on green and logs to ledger.

4) Team visibility
- Telegram posts a concise summary (debounced); peers stay quiet unless blocked.
- If you need files (screenshots/spec PDFs), drop them to the bot with a caption; peers act on the inbound block with meta.

RFD is not required here. It triggers automatically only for protected areas or when you explicitly ask for a decision.

## Recommended Stack (Pragmatic & Stable)

- AI CLIs: Claude Code (MAX Plan) + Codex CLI (PRO Plan) for robust, sustained workloads.
- Orchestrator: this project, with tmux for long‑lived panes and a compact panel.
- Transport & Governance: Telegram for team‑wide visibility, quick RFD decisions, and file exchange.

## Folder Layout (after `cccc init`)

```
.cccc/
  adapters/telegram_bridge.py    # Telegram long‑poll bridge (MVP)
  adapters/bridge_slack.py       # Slack bridge (Socket Mode + Web API, MVP)
  adapters/bridge_discord.py     # Discord bridge (Gateway + REST, MVP)
  adapters/outbox_consumer.py    # Shared Outbox reader (to_user/to_peer_summary)
  settings/
    cli_profiles.yaml            # tmux/paste/type behavior; echo; idle regexes; self‑check
    policies.yaml                # patch queue size; allowlist; RFD gates
    roles.yaml                   # leader; specialties; rotation
    telegram.yaml                # token/autostart/allowlist/dry_run/routing/files
    slack.yaml                   # app/bot tokens, channels, autostart, dry_run
    discord.yaml                 # bot token, channels, autostart, dry_run
  mailbox/                       # peerA/peerB with to_user.md/to_peer.md/patch.diff; inbox/processed
  work/                          # shared workspace; upload inbound/outbound; ephemeral
  state/                         # ledger.jsonl, bridge logs, status/session; ephemeral
  logs/                          # extra logs; ephemeral
  orchestrator_tmux.py delivery.py mailbox.py panel_status.py prompt_weaver.py
  evidence_runner.py mock_agent.py
```

## CLI Reference

- `cccc init [--force] [--to PATH]` — copy scaffold; preserves layout; excludes runtime dirs
- `cccc doctor` — check git/tmux/python/telegram
- `cccc run` — start orchestrator (tmux panes + status panel; optional bridge connect wizard; autostarts per YAML)
- `cccc token set|unset|show` — manage Telegram token (gitignored)
- `cccc bridge <telegram|slack|discord|all> start|stop|status|restart|logs [-n N] [--follow]` — control/inspect bridges
- `cccc clean` — purge `.cccc/{mailbox,work,logs,state}/`
- `cccc version` — show package version and scaffold path info

CLI prerequisites (summary)
- Peer A = Claude Code; Peer B = Codex CLI. Install and log in as required by each vendor.
- Ensure the binaries (`claude`, `codex`) are on PATH or set `commands.peer*`/`CLAUDE_I_CMD`/`CODEX_I_CMD`.

## Key Configuration (snippets)

`.cccc/settings/policies.yaml`

```
patch_queue:
  max_diff_lines: 150
  allowed_paths: ["src/**","tests/**","docs/**","infra/**","README.md","PROJECT.md"]
rfd:
  gates:
    protected_paths: [".cccc/**","src/api/public/**"]
    large_diff_requires_rfd: false
handoff_filter:
  enabled: true
  cooldown_seconds: 15
```

`.cccc/settings/telegram.yaml`

```
token_env: "TELEGRAM_BOT_TOKEN"
autostart: true
discover_allowlist: true
autoregister: open
dry_run: false
files:
  enabled: true
  outbound_dir: ".cccc/work/upload/outbound"
outbound:
  reset_on_start: clear
```

`.cccc/settings/slack.yaml`

```
app_token_env: SLACK_APP_TOKEN   # xapp-... (Socket Mode)
bot_token_env: SLACK_BOT_TOKEN   # xoxb-... (Web API)
autostart: false
dry_run: true
channels:
  to_user: []
  to_peer_summary: []
outbound:
  reset_on_start: baseline
```

`.cccc/settings/discord.yaml`

```
bot_token_env: DISCORD_BOT_TOKEN
autostart: false
dry_run: true
channels:
  to_user: []           # numeric channel IDs
  to_peer_summary: []
outbound:
  reset_on_start: baseline
```

## 0.2.9 Highlights (RC)

- Bridges: add Slack/Discord adapters (MVP). Outbound reads single‑source Outbox (`.cccc/state/outbox.jsonl`) via a shared consumer; inbound routes `a:/b:/both:` to mailbox inbox. Dry‑run by default; enable via `.cccc/settings/{slack,discord}.yaml` or env tokens.
- Unified bridge CLI: `cccc bridge <telegram|slack|discord|all> start|stop|status|restart|logs`。`cccc run` 启动向导可选择连接 Telegram/Slack/Discord；也可通过 YAML `autostart` 自启。
- Context maintenance: 每隔 N 次自检（`delivery.context_compact_every_self_checks`, 默认 5），向双端直通一次 `/compact`，随后立刻重注入完整 SYSTEM（首行含 “Now: … TZ”）。无需前置判断/重试。
- Self‑check 增强：自检首行注入当前时间/时区；新增第 4 条“是否把 insight 用于新维度（hook/assumption/risk/trade‑off/next/delta）而非复述”；第 5 条强化“广谱专家视角以避免狭隘执行”。
- NUDGE 改进：指数回退基于 180s，上限 60 分钟，`nudge_jitter_pct: 0.15`，无进展阈值 90s；NUDGE 文案鼓励在 inbox 为空时做高维度思考/微探针/小优化后继续推进。
- REV 硬门槛：出现 COUNTER/QUESTION 后，对端下一条 to_peer 必须为“合格 revise”（insight.kind=revise，且含 delta/refs/next，且非复述）或携带直接证据（内联统一 diff）。否则拦截并提示，记录 `revise-intercept`。
- 临时编码约束（PEERA）：为规避上游写文件编码顽固问题，PeerA 写入 `to_user.md`/`to_peer.md` 时使用英文 ASCII‑only（7‑bit）。问题解决后将移除该约束。

配置键（新增/调整）
- `.cccc/settings/cli_profiles.yaml` → `delivery.context_compact_every_self_checks: 5`（0=关闭）
- `.cccc/settings/cli_profiles.yaml` → NUDGE 相关：`nudge_backoff_base_ms: 180000`、`nudge_backoff_max_ms: 3600000`、`nudge_jitter_pct: 0.15`、`nudge_progress_timeout_s: 90`

## 0.2.7 Highlights (RC)

- Always‑on ```insight fenced block (formerly META): each peer appends a high‑level block to every message (1–2 blocks; first ask/counter preferred). Improves alignment without changing code state.
- Practical rule (single agent or peers): end every outbound message with exactly one trailing ```insight block (ask/counter preferred; include a concrete next step or ≤10‑min micro‑experiment). The orchestrator intercepts and asks you to overwrite and resend if the block is missing.
- Weekly Dev Diary: single weekly file `.cccc/work/docs/weekly/YYYY-Www.md` (PeerB writes). Daily create/replace today’s section ≤40 lines (Today/Changes/Risks‑Next). Next week’s first self‑check: add `## Retrospective` 3–5 bullets.
- Boot Context: initial SYSTEM includes current time/TZ and the weekly path; peers start with a shared anchor.
- Outbox Discipline: to_peer.md and to_user.md are overwrite‑only; the orchestrator (core) consumes and clears after logging/forwarding to avoid repeats. Bridges are thin mirrors, not state machines.

## FAQ / Troubleshooting

**tmux panes not appearing?**
- Install tmux (`tmux -V`), run in a TTY, then `cccc run`. Check `cccc doctor`.

**Telegram bot silent?**
- `cccc token show` (token saved?) → `cccc bridge status` (running?) → `cccc bridge logs -n 200`
- In group chats, route explicitly (`a:`/`/a`), and run `/whoami` or `/subscribe` once to register
- Ensure `autostart: true`, `dry_run: false`

**Claude/Codex CLI not found?**
- Install the CLIs and make sure the binaries are on PATH; otherwise set explicit commands:
  - Edit `.cccc/settings/cli_profiles.yaml` (`commands.peerA|peerB`) or
  - Export env vars before `cccc run`: `CLAUDE_I_CMD="/path/to/claude ..."`, `CODEX_I_CMD="/path/to/codex ..."`

**Where to put my project brief/policies?**
- Put your project scope/brief in `PROJECT.md` (repo root). The orchestrator injects it into the runtime SYSTEM so both peers align.
- RFD/protected paths live in `.cccc/settings/policies.yaml`.

**“This environment is externally managed” during install/build?**
- Use a venv or pipx for publishing; avoid system Python for `pip install` of tools like build/twine.

**RFD card not shown?**
- Confirm ledger has `kind:rfd`; check bridge logs; verify gates and to_peer YAML format.

## Security & Privacy

- Telegram token saved to `.cccc/settings/telegram.yaml` (gitignored) or env; do not commit secrets.
- Bridge redacts common secret patterns; keep mailbox content free of tokens.
- Orchestrator domain `.cccc/**` is runtime; do not commit `state/logs/work/mailbox`.

## Roadmap (Selected)

- Role‑based approvals; multi‑sign gates; richer RFD templates with default options/expiry
- Artifact previews in chat; repro snippets; CI/CD hooks for release/rollback cards
- Optional safety scanners (ClamAV/DLP) for inbound files; Slack/Mattermost bridges

## License

Apache (see LICENSE).
