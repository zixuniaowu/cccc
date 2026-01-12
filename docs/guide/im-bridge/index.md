# IM Bridge Overview

Bridge your CCCC working group to popular IM platforms for mobile access.

## What is IM Bridge?

The IM Bridge allows you to:

- Send messages to agents from your phone
- Receive updates and notifications
- Control the group with slash commands
- Share files and attachments

## Supported Platforms

| Platform | Status | Best For |
|----------|--------|----------|
| [Telegram](./telegram) | ✅ | Personal use, quick setup |
| [Slack](./slack) | ✅ | Team collaboration |
| [Discord](./discord) | ✅ | Community/gaming |
| [Feishu](./feishu) | ✅ | Enterprise (China) |
| [DingTalk](./dingtalk) | ✅ | Enterprise (China) |

## Design Principles

- **1 Group = 1 Bot**: Each working group connects to one bot instance for simplicity and isolation
- **Explicit subscription**: Users must `/subscribe` before receiving messages
- **Thin ports**: IM bridges only forward messages; the daemon is the single source of truth

## Common Commands

Once subscribed to any platform, these commands work universally:

| Command | Description |
|---------|-------------|
| (direct message) | Send to all agents |
| `@<actor> message` | Send to specific actor |
| `/subscribe` | Start receiving messages |
| `/unsubscribe` | Stop receiving messages |
| `/status` | Show group status |
| `/pause` | Pause message delivery |
| `/resume` | Resume message delivery |
| `/verbose` | Toggle verbose mode |
| `/help` | Show help |

## CLI Commands

```bash
# Configure (platform-specific, see each guide)
cccc im set <platform> --token-env <ENV_VAR>

# Control
cccc im start        # Start IM bridge
cccc im stop         # Stop IM bridge
cccc im status       # Check bridge status
cccc im logs         # View logs
cccc im logs -f      # Follow logs
```

## Quick Start

1. Choose a platform from the list above
2. Follow the setup guide to create a bot
3. Configure CCCC with the bot credentials
4. Start the bridge and subscribe in your chat

## Next Steps

- [Telegram Setup](./telegram) - Quick personal setup
- [Slack Setup](./slack) - Team collaboration
- [Discord Setup](./discord) - Community access
- [Feishu Setup](./feishu) - Enterprise (China)
- [DingTalk Setup](./dingtalk) - Enterprise (China)
