# Architecture

> CCCC = Collaborative Code Coordination Center
>
> A global AI Agent collaboration hub: a single daemon manages multiple working groups, with Web/CLI/IM as entry points.

## Core Concepts

### Working Group

- Like an IM group chat, but with execution/delivery capabilities
- Each group has an append-only ledger (event stream)
- Can bind multiple Scopes (project directories)

### Actor

- **Foreman**: Coordinator + Executor (the first enabled actor automatically becomes foreman)
- **Peer**: Independent expert (other actors)
- Supports PTY (terminal) and Headless (MCP-only) runners

### Ledger

- Single source of truth: `~/.cccc/groups/<group_id>/ledger.jsonl`
- All messages, events, and decisions are recorded here
- Supports snapshot/compaction

## Directory Layout

Default: `CCCC_HOME=~/.cccc`

```
~/.cccc/
├── registry.json                 # Working group index
├── daemon/
│   ├── ccccd.pid
│   ├── ccccd.log
│   └── ccccd.sock               # IPC socket
└── groups/<group_id>/
    ├── group.yaml               # Metadata
    ├── ledger.jsonl             # Event stream (append-only)
    ├── context/                 # Context (vision/sketch/tasks)
    └── state/                   # Runtime state
        └── ledger/blobs/        # Large text/attachments (referenced in ledger)
```

## Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│                      Ports (Entry)                       │
│   Web UI (React)  │  CLI  │  IM Bridge  │  MCP Server   │
├─────────────────────────────────────────────────────────┤
│                    Daemon (ccccd)                        │
│   IPC Server  │  Delivery  │  Automation  │  Runners    │
├─────────────────────────────────────────────────────────┤
│                      Kernel                              │
│   Group  │  Actor  │  Ledger  │  Inbox  │  Permissions  │
├─────────────────────────────────────────────────────────┤
│                    Contracts (v1)                        │
│   Event  │  Message  │  Actor  │  IPC                   │
└─────────────────────────────────────────────────────────┘
```

### Contracts Layer

- Pydantic models define all data structures
- Versioned: `src/cccc/contracts/v1/`
- Stable boundary, no business implementation

### Kernel

- Group/Scope/Ledger/Inbox/Permissions
- Depends on contracts, not on specific ports

### Daemon

- Single-writer principle: all ledger writes go through the daemon
- IPC + supervision + delivery/automation
- Manages actor lifecycle

### Ports (Entry)

- Only interact with daemon via IPC
- Hold no business state

## Ledger Schema (v1)

### Event Envelope

```jsonc
{
  "v": 1,
  "id": "event-id",
  "ts": "2025-01-01T00:00:00.000000Z",
  "kind": "chat.message",
  "group_id": "g_xxx",
  "scope_key": "s_xxx",
  "by": "user",
  "data": {}
}
```

### Known Kinds

| Kind | Description |
|------|-------------|
| `group.create/update/attach/start/stop` | Working group lifecycle |
| `actor.add/update/start/stop/restart/remove` | Actor lifecycle |
| `chat.message` | Chat message |
| `chat.read` | Read receipt |
| `system.notify` | System notification |

### chat.message Data

```python
class ChatMessageData:
    text: str
    format: "plain" | "markdown"
    to: list[str]           # Recipients (empty = broadcast)
    reply_to: str | None    # Reply to which message
    quote_text: str | None  # Quoted text
    attachments: list[dict] # Attachment metadata (content stored in CCCC_HOME blobs)
```

### Recipient Semantics (to field)

| Token | Semantics |
|-------|-----------|
| `[]` (empty) | Broadcast |
| `user` | The user |
| `@all` | All actors |
| `@peers` | All peers |
| `@foreman` | Foreman |
| `<actor_id>` | Specific actor |

## Files and Attachments

### Design Principles

- **Ledger stores only references, not large binaries**: Large text/attachments go to `CCCC_HOME` blobs (e.g., `groups/<group_id>/state/ledger/blobs/`).
- **No automatic writes to repo by default**: Attachments belong to the runtime domain (`CCCC_HOME`); if needed in scope/repo, user/agent explicitly copies/exports.
- **Content is portable**: Attachments use `sha256` as stable identity, allowing future cross-group/repo copy and reference rewriting.

## Roles and Permissions

### Role Definitions

- **Foreman = Coordinator + Worker**
  - Does actual work, not just task assignment
  - Extra coordination duties (receives actor_idle, silence_check notifications)
  - Can add/start/stop any actor

- **Peer = Independent Expert**
  - Has independent professional judgment
  - Can challenge foreman decisions
  - Can only manage self

### Permission Matrix

| Action | user | foreman | peer |
|--------|------|---------|------|
| actor_add | ✓ | ✓ | ✗ |
| actor_start | ✓ | ✓ (any) | ✗ |
| actor_stop | ✓ | ✓ (any) | ✓ (self) |
| actor_restart | ✓ | ✓ (any) | ✓ (self) |
| actor_remove | ✓ | ✓ (self) | ✓ (self) |

## MCP Server

38+ tools across 4 namespaces:

### cccc.* (Collaboration Control Plane)

- `cccc_inbox_list` / `cccc_inbox_mark_read`
- `cccc_message_send` / `cccc_message_reply`
- `cccc_group_info` / `cccc_actor_list`
- `cccc_actor_add/remove/start/stop/restart`
- `cccc_runtime_list` / `cccc_project_info`

### context.* (State Sync)

- `cccc_context_get` / `cccc_context_sync`
- `cccc_vision_update` / `cccc_sketch_update`
- `cccc_milestone_*` / `cccc_task_*`
- `cccc_note_*` / `cccc_reference_*`

### headless.* (Headless Runner)

- `cccc_headless_status` / `cccc_headless_set_status`
- `cccc_headless_ack_message`

### notify.* (System Notifications)

- `cccc_notify_send` / `cccc_notify_ack`

## Tech Stack

| Layer | Technology |
|-------|------------|
| Kernel/Daemon | Python + Pydantic |
| Web Port | FastAPI + Uvicorn |
| Web UI | React + TypeScript + Vite + Tailwind + xterm.js |
| MCP | stdio mode, JSON-RPC |

## Source Structure

```
src/cccc/
├── contracts/v1/          # Contracts layer
├── kernel/                # Kernel
├── daemon/                # Daemon process
├── runners/               # PTY/Headless runner
├── ports/
│   ├── web/              # Web port
│   ├── im/               # IM Bridge
│   └── mcp/              # MCP Server
└── resources/            # Built-in resources
```
