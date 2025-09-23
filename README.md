# CCCC Pair - Multi-Peer Orchestrator for Evidence-First Delivery

CCCC turns two AI CLIs into always-on collaborators that plan, build, and review as equals. You stay in control from tmux or your team chat; the peers keep rhythm, produce auditable evidence, and capture decisions in lightweight docs.

Not a chatbot UI. Not an IDE plugin. A production-minded orchestrator for long-running, real-world work.

## Why Teams Choose CCCC

- Multi-peer collaboration that compounds: peers alternate builder/critic, challenge each other, and converge by evidence (tests/logs/commits), not by talk.
- Agent-as-a-Service: agents run continuously with a small mailbox contract. They integrate with IM bridges (Telegram/Slack/Discord) without locking you into a custom UI.
- POR/SUBPOR anchors: one strategic board and simple per-task sheets keep everyone aligned without ceremony. You can read them in your repo under `docs/por/`.
- Low-noise, high-signal: built-in nudge and self-check cadence reduce chatter. The status panel shows what matters, including "Next self-check" and "Next auto-compact".
- tmux transparency: see both peers, watch the status panel, and intervene at any time. No GUI needed.
- Decisions you can audit: approvals and outcomes are logged; irreversible changes are explicit and deliberate.

## Core Ideas (in 60 seconds)

- Evidence-first: chats never change state; peers cite tests, stable log lines, or commit refs when claiming "done".
- Single contract: messages are `<TO_USER>` / `<TO_PEER>` blocks that end with one fenced `insight` (who/kind/next/refs). Simple, portable, tool-agnostic.
- Two anchors in your repo:
  - `docs/por/POR.md` (strategic board): North-star, deliverables, roadmap (Now/Next/Later), risk radar, recent decisions/pivots, and a short maintenance log.
  - `docs/por/T######-slug/SUBPOR.md` (per-task sheet): goal/scope, acceptance (3-5 checks), cheapest probe, kill criteria, implementation notes, REV log, next step.
- Optional third peer (Aux): an on-demand helper for big reviews or heavy lifting. Strategic Aux notes live in POR; tactical offloads sit in each task's SUBPOR.

## What You Can Do With It

- Ship small, reversible changes continuously with two peers driving; keep everything visible from tmux or IM.
- Keep strategy and execution in sync without meetings: POR captures the board; SUBPOR captures the work; both live in your repo.
- Bring the collaboration to where your team is: Telegram/Slack/Discord bridges handle routing, replies, and files.


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

tmux opens: left/right = PeerA/PeerB, bottom-right = status panel.
Run wizard (interactive TTY) lets you optionally connect a bridge:
- 1) Local only (default)
- 2) Local + Telegram
- 3) Local + Slack
- 4) Local + Discord
You can also manage bridges later via `cccc bridge ...` or set `autostart` in `.cccc/settings/*.yaml`.

5) First-time CLI setup (required)

- Install the two CLIs and make sure the binaries are on PATH:
  - `claude ...` (Claude Code)
  - `codex ...` (Codex CLI)
- Paste system prompts for best collaboration quality:
  - Open `PEERA.md` and copy its full content into a new file `CLAUDE.md` at your repo root (not tracked; `.gitignore` already ignores it). Claude Code CLI should load this as its system prompt.
  - Open `PEERB.md` and copy its full content into a new file `AGENTS.md` at your repo root (not tracked). Codex CLI should load this as its system prompt.
- Move any previous "project state" prompt into `PROJECT.md` at the repo root (recommended). The orchestrator will weave this into the runtime SYSTEM so both peers align on scope and goals.
- Verify or adjust the CLI commands in `.cccc/settings/cli_profiles.yaml`:
  - `commands.peerA: "claude ..."`
  - `commands.peerB: "codex ..."`
  - You can also override at runtime with env vars: `CLAUDE_I_CMD`, `CODEX_I_CMD`.

## First Landing Checklist (Minimal)

Do just these to get a clean, working setup:

1) `cccc init` (creates `./.cccc` and ignores runtime dirs)
2) `cccc doctor` (git/tmux/python)
3) Prepare system prompts (required once per repo)
   - Copy `PEERA.md` -> `CLAUDE.md` (root)
   - Copy `PEERB.md` -> `AGENTS.md` (root)
   - Put your brief/scope in `PROJECT.md` (root)
4) Optional Telegram (highly recommended)
   - `cccc token set`
   - `cccc run` (bridge autostarts) or `cccc bridge start`
5) Start work: `cccc run` (tmux panes + status panel)

That's it. You can refine policies later.

## IM Quickstart (Team Hub)

- Group routing: use explicit routes so normal chat stays normal
  - `a: <text>` / `b: <text>` / `both: <text>`
  - or `/a ...` `/b ...` `/both ...` (works with privacy mode)
- Get oriented
  - `/status` project stats; `/queue` handoff queue; `/locks` internal locks
  - `/whoami` shows your chat_id; `/subscribe` (if `autoregister: open`)
  - `/showpeers on|off` toggles Peer↔Peer summaries
- Control & passthrough
  - `/focus [hint]` asks PeerB to refresh `docs/por/POR.md`
  - `/reset compact|clear` issues manual compact/clear; `/review` triggers the aux reminder flow
  - `/aux status|on|off` inspects or toggles the optional third agent; the choice is persisted to `.cccc/settings/cli_profiles.yaml`
  - `/c <prompt>` (or `c: <prompt>`) runs the Aux GEMINI CLI with your prompt and returns the output
  - `a! <command>` / `b! <command>` sends a raw CLI command to PeerA/PeerB (non-interactive; advanced)
- File exchange
  - Outbound (AIs -> IM): save files to `.cccc/work/upload/outbound/` (flat)
  - Routing: either a `<name>.route` sidecar with `a|b|both`, or the first line of `<name>.caption.txt` starts with `a:`/`b:`/`both:` (the prefix is removed from the caption)
  - ACK: on success, a `<name>.sent.json` sidecar is written
  - Inbound (IM -> AIs): bridge writes `<FROM_USER>` with sidecar meta; peers act on it
- Governance: peers surface decisions in chat; once resolved, the outcome is logged to the ledger and work continues.

## A Typical Session (End-to-End, ~3 minutes)

Goal: ship a small, reversible change with dual-AI collaboration.

1) Explore (short)
- In Telegram (or tmux), route a brief idea to both: `both: Add a section to README about team chat tips`
- PeerA summarizes intent; PeerB asks 1 focused question if needed.

2) Decide (concise CLAIM)
- PeerA writes a CLAIM in `peerA/to_peer.md` with acceptance and constraints (link to where to edit if needed).
- PeerB COUNTERs if there's a sharper place or a safer rollout.

3) Build (evidence-first)
- PeerB proposes a small, verifiable change with a 1-2 line EVIDENCE note (tests OK / stable logs / commit refs).
- Orchestrator can run quick checks and logs outcomes to the ledger.

4) Team visibility
- Telegram posts a concise summary (debounced); peers stay quiet unless blocked.
- If you need files (screenshots/spec PDFs), drop them to the bot with a caption; peers act on the inbound block with meta.

No automatic decision prompts fire here; peers simply note the choices and ask for user guidance when needed.

## Recommended Stack (Pragmatic & Stable)

- AI CLIs: Claude Code (MAX Plan) + Codex CLI (PRO Plan) for robust, sustained workloads.
- Orchestrator: this project, with tmux for long-lived panes and a compact panel.
- Transport & Governance: Telegram for team-wide visibility, quick status sharing, and file exchange.

## Folder Layout (after `cccc init`)

```
.cccc/
  adapters/bridge_telegram.py    # Telegram long-poll bridge (MVP)
  adapters/bridge_slack.py       # Slack bridge (Socket Mode + Web API, MVP)
  adapters/bridge_discord.py     # Discord bridge (Gateway + REST, MVP)
  adapters/outbox_consumer.py    # Shared Outbox reader (to_user/to_peer_summary)
  settings/
    cli_profiles.yaml            # tmux/paste/type behavior; echo; idle regexes; self-check
    policies.yaml                # governance knobs; protected paths; redundancy filter
    governance.yaml              # POR/reset cadence; future governance knobs
    telegram.yaml                # token/autostart/allowlist/routing/files
    slack.yaml                   # app/bot tokens, channels, routing/files
    discord.yaml                 # bot token, channels, routing/files
  mailbox/                       # peerA/peerB with to_user.md/to_peer.md; inbox/processed
  work/                          # shared workspace; upload inbound/outbound; ephemeral
  state/                         # ledger.jsonl, bridge logs, status/session; ephemeral
  logs/                          # extra logs; ephemeral
  orchestrator_tmux.py delivery.py mailbox.py panel_status.py prompt_weaver.py
  evidence_runner.py mock_agent.py

docs/
  por/                           # POR and per-task SUBPOR sheets live here
    POR.md                       # strategic board (North-star, deliverables, roadmap, risks, decisions)
    T000123-your-task/SUBPOR.md  # per-task sheet (goal/acceptance/probe/kill/impl/REV/next)
```

## CLI Reference

- `cccc init [--force] [--to PATH]` - copy scaffold; preserves layout; excludes runtime dirs
- `cccc doctor` - check git/tmux/python/telegram
- `cccc run` - start orchestrator (tmux panes + status panel; optional bridge connect wizard; autostarts per YAML)
- `cccc token set|unset|show` - manage Telegram token (gitignored)
- `cccc bridge <telegram|slack|discord|all> start|stop|status|restart|logs [-n N] [--follow]` - control/inspect bridges
- `cccc clean` - purge `.cccc/{mailbox,work,logs,state}/`
- `cccc version` - show package version and scaffold path info

Adapter dependencies (optional)
- Slack bridge requires `slack_sdk` (install via `pip install slack_sdk`).
- Discord bridge requires `discord.py` (install via `pip install discord.py`).
If these packages or tokens are missing, adapters exit fast with a clear error. Slack inbound requires an App token (Socket Mode) while outbound requires a Bot token.

## POR/SUBPOR Anchors (in your repo)

- `docs/por/POR.md` is the strategic board: North-star, deliverables, roadmap (Now/Next/Later), risk radar, recent decisions/pivots, and a short maintenance log.
- `docs/por/T######-slug/SUBPOR.md` is a per-task sheet: goal/scope, 3-5 acceptance checks, cheapest probe, kill criteria, implementation notes, REV log, and the next step.
- Peers keep these brief and current as they work; you can read them at any time. They are lightweight and live with your code.

CLI prerequisites (summary)
- Peer A = Claude Code; Peer B = Codex CLI. Install and log in as required by each vendor.
- Ensure the binaries (`claude`, `codex`) are on PATH or set `commands.peer*`/`CLAUDE_I_CMD`/`CODEX_I_CMD`.

## Key Configuration (snippets)

`.cccc/settings/policies.yaml`

```
rfd:
  gates:
    protected_paths: [".cccc/**","src/api/public/**"]
handoff_filter:
  enabled: true
  cooldown_seconds: 15
```

`.cccc/settings/telegram.yaml`

```
token_env: TELEGRAM_BOT_TOKEN
autostart: true
discover_allowlist: true
autoregister: open           # open|off
allow_chats: []              # optional explicit allowlist (numeric chat IDs)
max_auto_subs: 3

show_peer_messages: true
default_route: both          # a|b|both

# Message sizing and pacing
debounce_seconds: 30
max_msg_chars: 4096
max_msg_lines: 32
peer_debounce_seconds: 30
peer_message_max_chars: 4096
peer_message_max_lines: 32

routing:
  require_explicit: true     # require a:/b:/both: (groups)
  allow_prefix: true         # allow a:/b:/both: prefixes
  require_mention: false     # require @BotName in groups (false = more convenient)

dm:
  route_default: both        # default when in DM

hints:
  cooldown_seconds: 300

files:
  enabled: true
  max_mb: 16
  allowed_mime: ["text/*","image/png","image/jpeg","application/pdf","application/zip"]
  inbound_dir: .cccc/work/upload/inbound
  outbound_dir: .cccc/work/upload/outbound

inbound_retention_days: 14
outbound_retention_days: 14
autowrap_from_user: true
run_clamav: false
redact_patterns:
  - (?i)api[_-]?key\s*[:=]\s*\S+
  - (?i)secret\s*[:=]\s*\S+
  - (?i)password\s*[:=]\s*\S+

outbound:
  reset_on_start: clear
```

`.cccc/settings/slack.yaml`

```
# Tokens
app_token_env: SLACK_APP_TOKEN   # xapp-... for Socket Mode (optional inbound)
bot_token_env: SLACK_BOT_TOKEN   # xoxb-... for Web API (required)

# Routing & display
show_peer_messages: true
default_route: both              # a|b|both when no explicit prefix

# Channels (channel IDs)
channels:
  to_user: []
  to_peer_summary: []

# Outbound
outbound:
  reset_on_start: clear          # baseline|clear

# Files
files:
  enabled: true
  max_mb: 16
  inbound_dir: .cccc/work/upload/inbound
  outbound_dir: .cccc/work/upload/outbound
```

`.cccc/settings/discord.yaml`

```
# Token
bot_token_env: DISCORD_BOT_TOKEN

# Routing & display
show_peer_messages: true
default_route: both              # a|b|both when no explicit prefix

# Channels (numeric IDs)
channels:
  to_user: []
  to_peer_summary: []

# Outbound
outbound:
  reset_on_start: clear          # baseline|clear

# Files
files:
  enabled: true
  max_mb: 16
  inbound_dir: .cccc/work/upload/inbound
  outbound_dir: .cccc/work/upload/outbound
```

## Feature Overview (selected)

- Bridges: Telegram (inbound/outbound), Slack (Socket Mode + Web API, MVP), Discord (Gateway + REST, MVP). Outbound reads single-source Outbox (`.cccc/state/outbox.jsonl`) via a shared consumer; inbound routes `a:/b:/both:` to mailbox inbox. No dry-run: tokens/SDKs are required and adapters fail fast when missing.
- Unified bridge CLI: `cccc bridge <telegram|slack|discord|all> start|stop|status|restart|logs` and an optional connect wizard in `cccc run`. Bridges can autostart via YAML.
- Context maintenance: on a cadence (config `delivery.context_compact_every_self_checks`), send `/compact` to both CLIs and immediately reinject the full SYSTEM with a leading "Now: ... TZ" line.
- Self-check enhancements: inject current time/TZ; add an insight-channel reminder to generate new angles (hook/assumption/risk/trade-off/next/delta) rather than restating.
- NUDGE improvements: exponential backoff, jitter, progress timeout; guidance for productive action when inbox is empty.
- REV gate: after a COUNTER/QUESTION, the next to_peer must be a valid revise (insight.kind=revise with delta/refs/next and not restating). Otherwise the message is intercepted with a short tip and logged.

## FAQ / Troubleshooting

**tmux panes not appearing?**
- Install tmux (`tmux -V`), run in a TTY, then `cccc run`. Check `cccc doctor`.

**Telegram bot silent?**
- `cccc token show` (token saved?) -> `cccc bridge status` (running?) -> `cccc bridge logs -n 200`
- In group chats, route explicitly (`a:`/`/a`), and run `/whoami` or `/subscribe` once to register
- Ensure `autostart: true`

**Claude/Codex CLI not found?**
- Install the CLIs and make sure the binaries are on PATH; otherwise set explicit commands:
  - Edit `.cccc/settings/cli_profiles.yaml` (`commands.peerA|peerB`) or
  - Export env vars before `cccc run`: `CLAUDE_I_CMD="/path/to/claude ..."`, `CODEX_I_CMD="/path/to/codex ..."`

**Where to put my project brief/policies?**
- Put your project scope/brief in `PROJECT.md` (repo root). The orchestrator injects it into the runtime SYSTEM so both peers align.
- Protected path patterns live in `.cccc/settings/policies.yaml` (auto gating disabled by default).

**"This environment is externally managed" during install/build?**
- Use a venv or pipx for publishing; avoid system Python for `pip install` of tools like build/twine.

**Decision log not shown?**
- Confirm ledger has `kind:rfd`; check bridge logs; verify gates and to_peer YAML format.

## Security & Privacy

- Telegram token saved to `.cccc/settings/telegram.yaml` (gitignored) or env; do not commit secrets.
- Bridge redacts common secret patterns; keep mailbox content free of tokens.
- Orchestrator domain `.cccc/**` is runtime; do not commit `state/logs/work/mailbox`.

## Roadmap (Selected)

- Role-based approvals; multi-sign gates; richer decision templates with default options/expiry
- Artifact previews in chat; repro snippets; CI/CD hooks for release/rollback cards
- Optional safety scanners (ClamAV/DLP) for inbound files; Slack/Mattermost bridges

## License

Apache (see LICENSE).
