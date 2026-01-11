# CLI Reference

Complete command reference for the CCCC CLI.

## Global Commands

### `cccc`

Start the daemon and Web UI together.

```bash
cccc                    # Start daemon + Web UI
cccc --help             # Show help
```

### `cccc doctor`

Check your environment and diagnose issues.

```bash
cccc doctor             # Full environment check
```

### `cccc runtime list`

List available agent runtimes.

```bash
cccc runtime list       # List detected runtimes
cccc runtime list --all # List all supported runtimes
```

## Daemon Commands

### `cccc daemon`

Manage the CCCC daemon.

```bash
cccc daemon status      # Check daemon status
cccc daemon start       # Start daemon
cccc daemon stop        # Stop daemon
cccc daemon restart     # Restart daemon
cccc daemon logs        # View daemon logs
cccc daemon logs -f     # Follow daemon logs
```

## Group Commands

### `cccc attach`

Create or attach to a working group.

```bash
cccc attach .           # Attach current directory as scope
cccc attach /path/to/project
```

### `cccc groups`

List all working groups.

```bash
cccc groups             # List groups
cccc groups --json      # JSON output
```

### `cccc use`

Switch to a different working group.

```bash
cccc use <group_id>     # Switch to group
```

### `cccc group`

Manage the current working group.

```bash
cccc group start        # Start all enabled actors
cccc group stop         # Stop all actors
cccc group info         # Show group info
cccc group edit         # Edit group settings
```

## Actor Commands

### `cccc actor add`

Add a new actor to the group.

```bash
cccc actor add <actor_id> --runtime claude
cccc actor add <actor_id> --runtime codex
cccc actor add <actor_id> --runtime custom --command "my-agent"
```

Options:
- `--runtime`: Agent runtime (claude, codex, droid, etc.)
- `--command`: Custom command (for custom runtime)
- `--runner`: Runner type (pty or headless)
- `--title`: Display title

### `cccc actor`

Manage actors.

```bash
cccc actor list                    # List actors
cccc actor start <actor_id>        # Start actor
cccc actor stop <actor_id>         # Stop actor
cccc actor restart <actor_id>      # Restart actor
cccc actor remove <actor_id>       # Remove actor
cccc actor edit <actor_id>         # Edit actor settings
```

## Message Commands

### `cccc send`

Send a message.

```bash
cccc send "Hello"                  # Broadcast to all
cccc send "Hello" --to @all        # Explicit broadcast
cccc send "Hello" --to foreman     # Send to foreman
cccc send "Hello" --to peer-1      # Send to specific actor
```

### `cccc reply`

Reply to a message.

```bash
cccc reply <event_id> "Reply text"
```

### `cccc inbox`

View inbox.

```bash
cccc inbox                         # View unread messages
cccc inbox --actor-id <id>         # View actor's inbox
cccc inbox --mark-read             # Mark all as read
```

### `cccc tail`

Tail the ledger.

```bash
cccc tail                          # Show recent events
cccc tail -n 50                    # Show last 50 events
cccc tail -f                       # Follow new events
```

## IM Bridge Commands

### `cccc im`

Manage IM Bridge.

```bash
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN
cccc im set slack --bot-token-env SLACK_BOT_TOKEN --app-token-env SLACK_APP_TOKEN
cccc im set discord --token-env DISCORD_BOT_TOKEN
cccc im set feishu --app-id-env FEISHU_APP_ID --app-secret-env FEISHU_APP_SECRET
cccc im set dingtalk --app-key-env DINGTALK_APP_KEY --app-secret-env DINGTALK_APP_SECRET

cccc im start                      # Start IM bridge
cccc im stop                       # Stop IM bridge
cccc im status                     # Check IM bridge status
cccc im logs                       # View IM bridge logs
cccc im logs -f                    # Follow IM bridge logs
```

## Setup Commands

### `cccc setup`

Configure MCP for an agent runtime.

```bash
cccc setup --runtime claude        # Auto-configure for Claude Code
cccc setup --runtime codex         # Auto-configure for Codex
cccc setup --runtime cursor        # Print manual config instructions
```

## Web Commands

### `cccc web`

Start only the Web UI (daemon must be running).

```bash
cccc web                           # Start Web UI
cccc web --port 9000               # Custom port
```

## MCP Commands

### `cccc mcp`

Start the MCP server (for agent integration).

```bash
cccc mcp                           # Start MCP server (stdio mode)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CCCC_HOME` | `~/.cccc` | Runtime home directory |
| `CCCC_WEB_HOST` | `127.0.0.1` | Web UI bind address |
| `CCCC_WEB_PORT` | `8848` | Web UI port |
| `CCCC_WEB_TOKEN` | (none) | Authentication token for Web UI |
| `CCCC_LOG_LEVEL` | `INFO` | Log level |
