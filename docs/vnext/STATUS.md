# CCCC vNext — Current Status & Roadmap (Living Doc)

> Goal: Upgrade CCCC from "per-repo runtime + tmux orchestration" to a **global delivery collaboration hub**: a single `ccccd` daemon managing multiple working groups, with Web/CLI/IM as entry points (ports), all key facts stored in group ledgers.

## 0) How far are we from "done"?

Current implementation is at **backend core complete, frontend needs polish** stage:

### ✅ Backend Complete
- Daemon architecture (single-writer principle)
- PTY + Headless runner (dual runner support)
- MCP Server (37 tools, 4 namespaces)
- IM-style messaging (reply, quote, read receipts)
- System notification layer (chat.message vs system.notify)
- Context sync (vision/sketch/milestones/tasks/notes/refs/presence)
- SYSTEM prompt injection (runner-type aware guidance)
- Message delivery (PTY direct injection + Headless notification)
- Ledger snapshot/compaction
- Multi-runtime support (claude, codex, droid, opencode)
- Foreman autonomy (can create/manage peers via MCP)

### ⚠️ Frontend To Polish (P1)
- Message search/pagination/virtual scroll (performance with many messages)
- Milestone/task editing in Web UI

### ✅ Recently Completed
- Message reply UI (click to reply, shows quote)
- System notification display (differentiate chat.message and system.notify)
- Actor runner type display (pty/headless badge)
- Actor runtime display (claude/codex/droid/opencode badge)
- `cccc setup --runtime <name>` command (auto-install skills and MCP config)
- Multi-runtime support: Claude Code, Codex CLI, Droid, OpenCode
- All code/config converted to English
- README documentation updated
- P0 Foreman autonomy: MCP tools for actor management (cccc_actor_add/remove/start/stop, cccc_runtime_list)
- P0 Runtime detection: `cccc doctor` and `cccc runtime list` commands
- P1 Web UI empty states and guidance
- P1 Runtime detection in Web UI (show available/unavailable runtimes)
- P1 Actor ID auto-suggestion based on runtime
- P1 Foreman uniqueness validation in actor add form
- P1 Context panel (view vision/sketch/milestones/tasks/presence)
- P1 Terminal interrupt button (Ctrl+C)
- P1 Group edit/delete (Web API + UI)
- P1 Context editing (vision/sketch via Web UI)
- P1 @mention autocomplete in composer
- P1 `cccc status` command (daemon, groups, actors overview)
- P1 Health check endpoint (`/api/v1/health`)
- P1 Auto-start daemon when running `cccc`
- P1 Scope list display (expandable details in header)
- P1 Keyboard shortcuts (Ctrl+Enter to send, Escape to cancel reply)
- P1 Graceful daemon shutdown (SIGTERM/SIGINT stops all actors)
- P1 Scope detach from Web UI (hover to show × button)
- P1 Web API: DELETE /api/v1/groups/{group_id}/scopes/{scope_key}
- P1 Actor edit modal (stop → edit runtime/command → start workflow)
- P1 Runtime template auto-fill (select runtime → command auto-filled, editable)
- P1 Read status display per recipient (✓ read / ○ unread inline)
- P1 Multi-line message input (textarea with auto-resize, Ctrl+Enter to send)
- P1 Visual message distinction (user=green, agent=blue, system=amber)
- P1 Scroll to bottom button (appears when scrolled up)
- P1 Settings panel (automation config: nudge/self-check/system-refresh intervals)
- P1 Actor unread message count display (inbox button shows count)
- P1 Actor presence status indicator (working=pulse, idle=amber, running=green)

### 🔜 Deferred Capabilities (P2)
- IM bridge (Telegram/Slack)
- RFD/decision/approval mechanism
- Multi-scope/multi-repo collaboration

## 1) vNext Vision (End State)

**Single Global Instance (`CCCC_HOME`)**
- Default `~/.cccc/` (override with `CCCC_HOME` env var)
- Multiple working groups: `CCCC_HOME/groups/<group_id>/`

**Single Resident Kernel (`ccccd`)**
- Single-writer: responsible for ledger writes, actor management, automation scheduling
- Crash recovery: rebuild runtime state from working directory (MVP: kill orphan PTY processes and auto-restart running groups)

**Multiple Ports (Web/CLI/IM/…)**
- Ports don't hold truth: all read/write through daemon
- Web: group chat console + lightweight intervention (terminal when needed)
- CLI: scripting, quick ops, troubleshooting
- IM: remote notification/control (deferred)

## 2) Implemented (Runnable Capabilities)

### 2.1 Daemon / Runtime
- ✅ Global daemon: `ccccd` (unix socket IPC)
- ✅ Global home: `CCCC_HOME` (groups/daemon directory structure)
- ✅ PTY runner: daemon manages each actor's PTY session (no tmux)
- ✅ Headless runner: no PTY, pure MCP-driven agent
- ✅ Crash cleanup: daemon kills orphan PTY on startup via pidfile
- ✅ Desired run-state: `group.yaml: running=true/false`, daemon auto-starts running groups

### 2.2 Working Group / Actor
- ✅ group: create / update(title/topic) / attach(scope) / start / stop / delete
- ✅ actor: add / update / start/stop/restart / remove
- ✅ Permissions (simple): peer can only manage self; foreman can manage group peers
- ✅ Runner type: `pty` (default) or `headless`
- ✅ Runtime type: `claude` / `codex` / `droid` / `opencode` / `custom`

### 2.3 Ledger (Event Stream)
- ✅ ledger.jsonl: unified envelope (`v/id/ts/kind/group_id/scope_key/by/data`)
- ✅ Known kind data structure validation (contracts v1)
- ✅ Large event handling: oversized chat text stored in `state/ledger/blobs/`
- ✅ Snapshot/Compaction: auto-archive read events

### 2.4 IM-Style Messaging
- ✅ Reply messages: `chat.message.data.reply_to` + `quote_text`
- ✅ Read receipts: `chat.read` event
- ✅ CLI support: `cccc reply <event_id> "text"`
- ✅ Delivery format: `[cccc] <by> → <to> (reply:xxx): <text>`

### 2.5 System Notification Layer
- ✅ Message separation: `chat.message` (user conversation) vs `system.notify` (system notifications)
- ✅ Notification types: `nudge` / `self_check` / `system_refresh` / `status_change` / `error` / `info`
- ✅ Priority: `low` / `normal` / `high` / `urgent` (high/urgent delivered directly to PTY)
- ✅ Acknowledgment: `requires_ack` + `system.notify_ack` event
- ✅ Inbox filtering: `kind_filter` parameter supports `all` / `chat` / `notify`

### 2.6 Delivery & SYSTEM Injection
- ✅ PTY runner: direct terminal injection (bracketed-paste or file fallback)
- ✅ Headless runner: notification via `system.notify` event
- ✅ SYSTEM injection: inject `render_system_prompt()` on actor start/restart
- ✅ Runner-aware prompt: PTY and headless have different guidance content

### 2.7 Automation (Simplified)
- ✅ NUDGE: inject reminder when actor inbox has unread messages timeout
- ✅ SELF-CHECK: trigger self-check prompt every N deliveries
- ✅ Automation events written to ledger via `system.notify`

### 2.8 MCP Port (37 Tools)
- ✅ MCP server: `cccc mcp` (stdio mode)
- ✅ Architecture: all operations via daemon IPC, ensuring single-writer principle
- ✅ **cccc.* namespace** (collaboration control plane):
  - `cccc_inbox_list` / `cccc_inbox_mark_read`
  - `cccc_message_send` / `cccc_message_reply`
  - `cccc_group_info` / `cccc_actor_list`
  - `cccc_actor_add` / `cccc_actor_remove` / `cccc_actor_start` / `cccc_actor_stop`
  - `cccc_runtime_list`
- ✅ **context.* namespace** (state sync):
  - `cccc_context_get` / `cccc_context_sync`
  - `cccc_vision_update` / `cccc_sketch_update`
  - `cccc_milestone_*` / `cccc_task_*`
  - `cccc_note_*` / `cccc_reference_*`
  - `cccc_presence_*`
- ✅ **headless.* namespace** (headless runner control):
  - `cccc_headless_status` / `cccc_headless_set_status` / `cccc_headless_ack_message`
- ✅ **notify.* namespace** (system notifications):
  - `cccc_notify_send` / `cccc_notify_ack`

### 2.9 Web Port (Basic)
- ✅ FastAPI: REST + SSE ledger stream + WS terminal
- ✅ Web UI (React/Vite + xterm.js):
  - Group list + create/select
  - Actor management (add/remove/start/stop/inbox/term)
  - Event stream display (with event type differentiation)
  - Message sending (with recipient selection)
  - Message reply UI (click to reply, shows quote)
  - Inbox modal
  - Terminal modal
  - Runtime badge display (claude/codex/droid/opencode)

### 2.10 Headless Runner
- ✅ Runner abstraction: Actor supports `pty` or `headless` runner
- ✅ State machine: `idle` → `working` → `waiting` → `stopped`
- ✅ MCP tools: headless agent controls own state via MCP
- ✅ Message notification: daemon sends `system.notify` on new messages
- ✅ State persistence: `~/.cccc/groups/<group_id>/state/runners/headless/<actor_id>.json`

### 2.11 Multi-Runtime Support
- ✅ Supported runtimes: `claude`, `codex`, `droid`, `opencode`, `custom`
- ✅ `cccc setup --runtime <name>`: auto-install skills and configure MCP
- ✅ `cccc actor add --runtime <name>`: auto-set command based on runtime
- ✅ CLI-based MCP config: claude/codex/droid support `<cli> mcp add` command
- ✅ Manual config guidance: opencode requires manual MCP configuration

## 3) Not Yet Complete (Detailed Gap Analysis)

> See `docs/vnext/USER_FLOW_ANALYSIS.md` for complete step-by-step user journey analysis.

### P0: Critical (Blocking Core Functionality)

#### Foreman Autonomy (Cannot create peers)
- [x] **cccc_actor_add** MCP tool - Foreman can autonomously create peers
- [x] **cccc_actor_remove** MCP tool - Foreman can remove peers
- [x] **cccc_actor_start** / **cccc_actor_stop** MCP tools - Foreman can manage peer lifecycle
- [x] **cccc_runtime_list** MCP tool - Foreman can discover available agent CLIs
- [x] SYSTEM prompt guidance for foreman on how to create and manage peers

#### Runtime Detection
- [x] `cccc doctor` command - Environment verification with runtime detection
- [x] `cccc runtime list` CLI command - List available agent CLIs
- [x] Runtime auto-detection using `shutil.which()` for claude/codex/droid/opencode binaries

### P1: Important (Usability)

#### Onboarding & First-Run Experience
- [ ] First-time setup wizard (detect CLIs, suggest quick setup)
- [x] `cccc --version` command (via `cccc version`)
- [ ] Post-install welcome message with quick-start hints
- [ ] Setup validation (verify MCP config before starting actor)

#### Web UI - Empty States & Guidance
- [x] Empty state for no groups: "No working groups yet. Create one to get started."
- [x] Empty state for no actors: "No actors yet. Click + actor to add an agent."
- [ ] Explanation of what a "working group" is
- [x] Explanation of foreman vs peer roles (tooltip/help text)
- [x] Explanation of pty vs headless runners (tooltip/help text)

#### Web UI - Actor Setup
- [x] Show which runtimes are available (detected on system)
- [x] Gray out unavailable runtime options
- [x] Auto-generate actor ID suggestion based on runtime
- [x] Validation feedback (e.g., "foreman already exists")
- [ ] Actor setup wizard (step-by-step flow)

#### Web UI - Group Management
- [x] Group edit modal (title/topic)
- [x] Group delete with confirmation
- [x] Web API: PUT /api/v1/groups/{group_id}
- [x] Web API: DELETE /api/v1/groups/{group_id}

#### Web UI - Context & Presence
- [x] Context panel (view vision/sketch/milestones/tasks/presence)
- [x] Context editing (update vision/sketch via UI)
- [ ] Real-time activity indicator per actor

#### Web UI - Polish
- [ ] Message search/pagination/virtual scroll
- [ ] Settings panel (group settings, actor settings, automation config)
- [x] @mention autocomplete in composer
- [ ] Delivery status indicators (sent/delivered/read)
- [x] Interrupt button in terminal modal (Ctrl+C equivalent)
- [x] Keyboard shortcuts (Ctrl+Enter to send, Escape to cancel)
- [x] Scope list display (expandable details in header)
- [x] Scope detach from UI (hover to show × button)

### P2: Nice to Have

#### Productization
- [ ] PyPI packaging (`pip install cccc`)
- [ ] Homebrew formula for macOS
- [ ] IM bridge (Telegram/Slack) bound per group
- [ ] Unified auth/token UX

#### Advanced Features
- [ ] RFD/decision/approval mechanism
- [ ] Multi-scope/multi-repo collaboration
- [ ] Terminal history/scrollback persistence

### Old v0.3.28 Features to Consider

From the legacy implementation:

1. **agents.yaml** - Predefined agent configurations:
   - Command templates per runtime (claude, codex, droid, opencode, gemini, etc.)
   - Input mode, post-paste keys, send sequence
   - Capabilities description per agent
   - Environment requirements

2. **cli_profiles.yaml** - Role bindings:
   - Which actor each role uses
   - Inbound/outbound message suffixes
   - Nudge configuration

3. **Doctor command** - Environment verification:
   - Check git, tmux, python availability
   - Check agent CLI availability with `shutil.which()`
   - Show roles and their status

4. **Roles wizard** - Interactive setup with timeout-based prompts

## 4) Next Steps (Prioritized)

### ✅ Completed (P0)
1. ~~**cccc_actor_add MCP tool**~~ - Foreman can create peers autonomously
2. ~~**cccc_runtime_list MCP tool**~~ - Foreman can discover available agent CLIs
3. ~~**cccc doctor CLI command**~~ - Environment verification with runtime detection
4. ~~**Update SYSTEM prompt**~~ - Guide foreman on peer creation workflow

### ✅ Completed (P1)
5. ~~**Web UI empty states**~~ - Guidance for new users
6. ~~**Runtime detection in Web UI**~~ - Show available/unavailable runtimes
7. ~~**Context panel**~~ - Display context (vision/sketch/milestones/tasks/presence)
8. ~~**Terminal interrupt button**~~ - Ctrl+C equivalent in web terminal

### Short-term (P1 - Next Sprint)
9. **Context editing** - Update vision/sketch via Web UI
10. **Message search/pagination** - Performance with many messages
11. **Settings panel** - Configuration UI
12. **Real-time presence indicators** - Show agent activity in real-time

### Medium-term (P2)
13. **Actor setup wizard** - Step-by-step UI flow
14. **End-to-end testing** - Validate complete workflow
15. **PyPI packaging** - `pip install cccc`

## 5) Tech Stack

- **Kernel/Daemon**: Python + Pydantic
- **Web Port**: FastAPI + Uvicorn
- **Web UI**: React + TypeScript + Vite + Tailwind + xterm.js
- **MCP**: stdio mode, JSON-RPC
