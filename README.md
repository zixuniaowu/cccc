# CCCC vNext — Multi-Agent Delivery Kernel

CCCC is a multi-agent collaboration delivery kernel that enables multiple agent CLIs (Claude Code, Codex, Droid, OpenCode, etc.) to work together on the same project.

## Core Concepts

- **Working Group**: Collaboration unit (like an IM group chat), contains multiple actors
- **Actor**: An agent session (PTY or headless mode)
- **Ledger**: Event stream (append-only), records all messages and events
- **Scope**: Project root directory, determines actor's working directory
- **Foreman**: First enabled actor automatically becomes foreman (can manage other peers)
- **Peer**: Other actors are peers (execute tasks)

## Quick Start

```bash
# Install
pip install cccc-pair
# or for development
pip install -e .

# Check environment
cccc doctor

# Initialize project (create group and attach current directory)
cccc attach .

# Setup MCP for your agent runtime
cccc setup --runtime claude  # or codex, droid, opencode

# Start daemon
cccc daemon start

# Add actors (first actor becomes foreman automatically)
cccc actor add main-agent --runtime claude
cccc actor add peer-agent --runtime codex

# Start group (starts all actors)
cccc group start

# Open Web console
cccc
```

Open `http://127.0.0.1:8848/ui/`

## Actor Role (Auto-determined)

Role is automatically determined by position in the actor list:
- **First enabled actor** = Foreman (can manage other actors)
- **All other actors** = Peer

No manual role assignment needed. To change foreman, disable the current first actor or remove it.

## Supported Agent Runtimes

| Runtime | Command | Description |
|---------|---------|-------------|
| Claude Code | `claude` | Anthropic official CLI |
| Codex CLI | `codex` | OpenAI Codex CLI |
| Droid | `droid` | Droid agent CLI |
| OpenCode | `opencode` | Open source agent CLI |
| Custom | any | Any shell command |

### Adding Actors

```bash
# Using runtime presets (auto-sets command)
# First actor becomes foreman automatically
cccc actor add main-agent --runtime claude
cccc actor add impl-agent --runtime codex

# Custom command
cccc actor add custom-agent --command "aider --model gpt-4"

# Headless actor (MCP-only, no PTY)
cccc actor add api-agent --runner headless
```

## Auto Setup

`cccc setup` command automatically:

1. **Installs Skills**: Creates `cccc-ops` skill in project
2. **Configures MCP**: Generates MCP configuration

```bash
# Setup for specific runtime
cccc setup --runtime claude
cccc setup --runtime codex
cccc setup --runtime droid
cccc setup --runtime opencode
```

## MCP Tools (37 tools, 4 namespaces)

### cccc.* (Collaboration)
- `cccc_inbox_list` / `cccc_inbox_mark_read`: Message inbox
- `cccc_message_send` / `cccc_message_reply`: Send/reply messages
- `cccc_group_info` / `cccc_group_set_state`: Group info and state control
- `cccc_actor_list`: List actors
- `cccc_actor_add` / `cccc_actor_remove`: Manage actors (foreman only)
- `cccc_actor_start` / `cccc_actor_stop`: Actor lifecycle
- `cccc_runtime_list`: List available runtimes
- `cccc_project_info`: Get PROJECT.md content

### context.* (State Sync)
- `cccc_context_get` / `cccc_context_sync`: Get/sync context
- `cccc_task_*`: Task management
- `cccc_milestone_*`: Milestone management
- `cccc_note_*` / `cccc_reference_*`: Notes and references
- `cccc_presence_*`: Presence status

### headless.* (Headless Runner)
- `cccc_headless_status` / `cccc_headless_set_status`: Status management
- `cccc_headless_ack_message`: Message acknowledgment

### notify.* (System Notifications)
- `cccc_notify_send` / `cccc_notify_ack`: System notifications

## CLI Commands

```bash
# Environment
cccc doctor                      # Check environment and runtimes
cccc runtime list                # List available agent CLIs

# Group management
cccc attach .                    # Attach current directory to group
cccc groups                      # List all groups
cccc group create --title "xxx"  # Create group
cccc group start                 # Start group
cccc group stop                  # Stop group
cccc group set-state <state>     # Set state: active|idle|paused

# Actor management
cccc actor list                  # List actors
cccc actor add <id> --runtime claude  # First actor = foreman
cccc actor start <id>            # Start actor
cccc actor stop <id>             # Stop actor

# Messaging
cccc send "hello" --to @all      # Send message
cccc reply <event_id> "reply"    # Reply to message
cccc inbox --actor-id <id>       # View inbox
cccc tail -n 50                  # View ledger

# Other
cccc daemon start|stop|status    # Manage daemon
cccc web                         # Start Web console
cccc mcp                         # Start MCP server (stdio)
cccc setup --runtime <name>      # Setup skills and MCP
cccc version                     # Show version
```

## Architecture

```
~/.cccc/                         # CCCC_HOME
  daemon/                        # Daemon runtime
    ccccd.sock                   # IPC socket
  groups/
    <group_id>/
      group.yaml                 # Group metadata
      ledger.jsonl               # Event stream
      context/                   # Context data
      state/                     # Runtime state
```

## Documentation

- [Design Doc](docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md)
- [Ledger Schema](docs/vnext/LEDGER_SCHEMA.md)
- [Agent Guidance](docs/vnext/AGENT_GUIDANCE.md)
- [IM Messaging](docs/vnext/IM_MESSAGING.md)
- [Status](docs/vnext/STATUS.md)

## License

Apache-2.0
