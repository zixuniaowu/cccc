# CCCC 0.4.x (RC) — Global Multi-Agent Delivery Kernel

**English** | [中文](README.zh-CN.md) | [日本語](README.ja.md)

> Status: **0.4.0rc10** (Release Candidate). Expect breaking changes while we harden UX and contracts.

CCCC is a **local-first multi-agent collaboration kernel** that feels like a modern IM, but stays reliable because it is backed by:

- A single-writer daemon (one source of truth)
- An append-only ledger per working group (durable history)
- An MCP tool surface for agents (no “stdout chat” ambiguity)

At a glance:

- A single daemon (`ccccd`) coordinates many agent runtimes (Claude Code, Codex CLI, Droid, OpenCode, Copilot, …)
- Each working group has an **append-only ledger** as the source of truth
- A bundled **Web UI** is the control plane (mobile-first responsive UI)
- A built-in **MCP stdio server** lets agents operate CCCC via tools (reliable message delivery; no “stdout chat” ambiguity)

Legacy tmux/TUI version (v0.3.x): https://github.com/ChesterRa/cccc-tmux

---

## Screenshots

Chat UI:

![CCCC Chat UI](screenshots/chat.png)

Agent terminal UI:

![CCCC Agent Terminal](screenshots/terminal.png)

## Why a rewrite (from v0.3.x)?

v0.3.x (tmux-first) proved the loop, but it hit real product limits:

1) **No unified ledger**  
   Messages lived in multiple per-actor/per-channel files. After each delivery, agents often had to re-open files (or fetch full content) to continue, which increases latency and friction.

2) **Hard limit on actor count**  
   The tmux layout strongly nudged toward 1–2 actors. Scaling beyond that becomes a UI/ops problem.

3) **Weak “agent can control the system” surface**  
   The old line lacked a complete tool surface for agents to manage the system. That limits autonomy: agents can’t reliably adjust group/actor/settings the same way a user can.

4) **Remote access was not a first-class experience**  
   Tmux works locally, but it doesn’t translate to “open from phone/laptop anywhere” without a lot of manual glue. A Web control plane is simply the right primitive here.

0.4.x rewrites the kernel boundary:

- **Unified ledger**: every group has an append-only ledger as the single source of truth.
- **N-actor model**: a group can host many actors; add/start/stop/relaunch are first-class operations.
- **MCP control plane**: agents can manage and operate CCCC via tools (messages, context, actors, group state, etc.).
- **Web-first console**: remote access becomes feasible via standard HTTP + your preferred tunnel/VPN (Cloudflare/Tailscale/WireGuard).
- **IM-grade messaging UX**: user↔agent communication is designed like a modern IM—@mentions routing, reply/quote, explicit read/ack, consistent behavior across Web UI and IM bridges.
- **One runtime home**: `CCCC_HOME` (default `~/.cccc/`) stores groups, ledgers, and runtime state.
- **One writer**: the daemon is the only writer to the ledger; ports stay thin.

Honest trade-offs:

- 0.4.x is daemon-based (a long-lived local service).
- 0.4.x is RC, so we prioritize correctness and UX consistency over feature count.
- If you want the old tmux workflow, use `cccc-tmux` (v0.3.x).

---

## Core concepts

- **Working Group**: collaboration unit (like a group chat) with durable history + automation.
- **Actor**: an agent session (PTY or headless).
- **Scope**: a directory URL attached to a group; each event is attributed with a `scope_key`.
- **Ledger**: append-only event stream; messages and state changes are first-class events.
- **`CCCC_HOME`**: global runtime home (default `~/.cccc/`).

Runtime layout (default):

```text
~/.cccc/
  daemon/
    ccccd.sock
    ccccd.log
  groups/<group_id>/
    group.yaml
    ledger.jsonl
    context/
    state/
```

---

## Requirements

- Python 3.9+
- macOS / Linux (Windows via WSL recommended)
- At least one supported agent runtime CLI installed (Claude/Codex/Droid/OpenCode/Copilot/…)
- Node.js is only needed for **Web UI development** (end users get a bundled UI)

---

## Installation

### Install 0.4.x RC from TestPyPI (recommended today)

RC tags (e.g. `v0.4.0-rc10`) are published to **TestPyPI**. Use PyPI for dependencies, and TestPyPI only for the RC package:

```bash
python -m pip install --index-url https://pypi.org/simple \
  --extra-index-url https://test.pypi.org/simple \
  cccc-pair==0.4.0rc10
```

Note: at the moment, the latest stable on PyPI is still the legacy v0.3.x line. Use the command above to install 0.4.x RCs.

### Install from source (development)

```bash
git clone https://github.com/ChesterRa/cccc
cd cccc
pip install -e .
```

Web UI development (optional):

```bash
cd web
npm install
npm run dev
```

---

## Quick start (local)

```bash
# 1) Choose a repo (scope)
cd /path/to/repo

# 2) Create/attach a working group
cccc attach .

# 3) Configure MCP for your runtime (recommended)
cccc setup --runtime claude   # or codex, droid, opencode, copilot, ...

# 4) Add actors (first enabled actor becomes foreman)
cccc actor add foreman --runtime claude
cccc actor add peer-1  --runtime codex

# 5) Start the group (spawns enabled actors)
cccc group start

# 6) Start daemon + Web console (Ctrl+C stops both)
cccc
```

Open `http://127.0.0.1:8848/` (redirects to `/ui/`).

---

## Runtimes and MCP setup

CCCC is runtime-agnostic, but MCP setup differs by CLI.

- Auto MCP setup: `claude`, `codex`, `droid`, `amp`, `auggie`, `neovate`, `gemini`
- Manual MCP setup (CCCC prints exact instructions): `cursor`, `kilocode`, `opencode`, `copilot`, `custom`

Use:

```bash
cccc runtime list --all
cccc setup --runtime <name>
```

Recommended default commands for autonomous operation (override per actor as needed):

- Claude Code: `claude --dangerously-skip-permissions`
- Codex CLI: `codex --dangerously-bypass-approvals-and-sandbox --search`
- Copilot CLI: `copilot --allow-all-tools --allow-all-paths`

---

## Web UI (mobile-first)

The bundled Web UI is the primary control plane:

- Multi-group navigation
- Actor management (add/start/stop/relaunch)
- Chat with @mentions + reply
- Embedded terminal per actor (PTY runner)
- Context + automation settings
- IM Bridge config
- PROJECT.md view/edit (repo root)

---

## PROJECT.md (project constitution)

Place `PROJECT.md` at the scope root (repo root). Treat it as the project constitution:

- Agents should read it early (MCP tool: `cccc_project_info`).
- Web UI can view/edit/create it, but agents should **not** edit it unless the user explicitly asks.

---

## IM Bridge (Telegram / Slack / Discord)

CCCC can bridge a working group to an IM platform.

- Subscriptions are explicit (e.g. send `/subscribe` in the chat).
- Attachments are stored under `CCCC_HOME` blobs and referenced in the ledger (not written into your repo by default).

Configure via Web UI (Settings → IM Bridge) or CLI:

```bash
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN
cccc im start
```

---

## Security notes (remote / phone access)

The Web UI is high privilege (it can control actors and access project files). If you expose it remotely:

- Set `CCCC_WEB_TOKEN` and put it behind an access gateway (Cloudflare Access, Tailscale, WireGuard, …).
- Do not expose an unauthenticated local port to the public internet.

---

## CLI cheat sheet

```bash
cccc doctor
cccc runtime list --all
cccc groups
cccc use <group_id>

cccc send "hello" --to @all
cccc reply <event_id> "reply text"
cccc inbox --actor-id <id> --mark-read
cccc tail -n 50 -f

cccc daemon status|start|stop
cccc mcp
```

---

## Docs

- `docs/vnext/README.md` (entry)
- `docs/vnext/ARCHITECTURE.md`
- `docs/vnext/FEATURES.md`
- `docs/vnext/STATUS.md`
- `docs/vnext/RELEASE.md` (maintainers)

## License

Apache-2.0
