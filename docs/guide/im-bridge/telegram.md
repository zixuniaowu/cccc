# Telegram Setup

Connect your CCCC working group to Telegram for mobile access.

## Overview

Telegram is the easiest platform to set up. It's ideal for:

- Personal use
- Quick prototyping
- Individual developers

## Prerequisites

- A Telegram account
- CCCC installed and running

## Step 1: Create a Bot

1. Open Telegram and search for `@BotFather`
2. Start a chat and send `/newbot`
3. Follow the prompts:
   - Choose a display name (e.g., "My CCCC Bot")
   - Choose a username (must end in `bot`, e.g., `my_cccc_bot`)
4. BotFather will give you a token like:
   ```
   123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   ```
5. **Save this token securely** - it's your bot's password

## Step 2: Configure Bot Settings (Optional)

Still in BotFather, you can customize your bot:

```
/setdescription - Set bot description
/setabouttext - Set about text
/setuserpic - Set profile picture
```

### Recommended Settings

Disable group privacy to allow the bot to see all messages in groups:

1. Send `/mybots` to BotFather
2. Select your bot
3. Go to **Bot Settings** → **Group Privacy**
4. Set to **Disabled**

## Step 3: Set Environment Variable

Store the token in an environment variable:

```bash
# Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
export TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

# Or set it for the current session
export TELEGRAM_BOT_TOKEN="your-token-here"
```

::: warning Security
Never commit tokens to git. Use environment variables or a secrets manager.
:::

## Step 4: Configure CCCC

### Option A: Via Web UI

1. Open the CCCC Web UI at `http://127.0.0.1:8848/`
2. Go to **Settings** (gear icon in header)
3. Navigate to the **IM Bridge** section
4. Select **Telegram** as the platform
5. Enter your credentials:
   - **Token Environment Variable**: `TELEGRAM_BOT_TOKEN`
6. Click **Save**

### Option B: Via CLI

```bash
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN
```

Both methods save the configuration to your group's `group.yaml`:

```yaml
im:
  platform: telegram
  token_env: TELEGRAM_BOT_TOKEN
```

## Step 5: Start the Bridge

```bash
cccc im start
```

Verify it's running:

```bash
cccc im status
```

## Step 6: Subscribe in Telegram

1. Open Telegram and find your bot (search by username)
2. Start a chat with the bot
3. Send `/subscribe`
4. You should receive a confirmation message

For group chats:
1. Add the bot to your group
2. Send `/subscribe` in the group
3. All subscribed chats receive messages from CCCC

## Usage

### Sending Messages to Agents

In group chats, @mention the bot first, then use the `/send` command:

```
@YourBotName /send Please implement the login feature
```

In direct messages with the bot, you can use `/send` directly:

```
/send Please implement the login feature
```

::: warning Important
- In group chats, you must @mention the bot before using commands
- Plain messages without the `/send` command are ignored
:::

### Targeting Specific Agents

Use `@mention` syntax with the `/send` command:

```
/send @foreman Please review the PR
/send @peer-1 Run the tests
/send @all Status update please
```

### Receiving Messages

After subscribing, you will automatically receive:
- Agent responses
- Status updates
- Error notifications

Use `/verbose` to toggle whether you see agent-to-agent messages.

### File Attachments

Attach files to your message. They're downloaded and stored in CCCC's blob storage, then forwarded to agents.

## Commands Reference

| Command | Description |
|---------|-------------|
| `/subscribe` | Start receiving messages from CCCC |
| `/unsubscribe` | Stop receiving messages |
| `/send <message>` | Send to foreman (default) |
| `/send @<actor> <message>` | Send to a specific agent |
| `/send @all <message>` | Send to all agents |
| `/send @peers <message>` | Send to non-foreman agents |
| `/status` | Show group and agent status |
| `/pause` | Pause message delivery |
| `/resume` | Resume message delivery |
| `/verbose` | Toggle verbose mode (see all agent messages) |
| `/help` | Show available commands |

## Troubleshooting

### Bot not responding

1. Check if the bridge is running:
   ```bash
   cccc im status
   ```

2. Check logs for errors:
   ```bash
   cccc im logs -f
   ```

3. Verify token is correct:
   ```bash
   echo $TELEGRAM_BOT_TOKEN
   ```

### "Unauthorized" error

Your token is invalid. Get a new one from BotFather:

1. Send `/mybots` to BotFather
2. Select your bot
3. Click **API Token** → **Revoke current token**
4. Update your environment variable

### Messages not delivered

1. Ensure you've sent `/subscribe`
2. Check that the daemon is running: `cccc daemon status`
3. Verify the group is correct: `cccc group info`

### Rate limiting

Telegram has rate limits. If you're sending many messages:
- Messages may be delayed
- Consider using `/verbose off` to reduce traffic

## Advanced Configuration

### Multiple Groups

Each working group can have its own Telegram bot:

```bash
# Switch to group 1
cccc use group-1
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN_1

# Switch to group 2
cccc use group-2
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN_2
```

### Webhook Mode (Advanced)

By default, CCCC uses long polling. For production, you can use webhooks:

```yaml
# group.yaml
im:
  platform: telegram
  token_env: TELEGRAM_BOT_TOKEN
  webhook_url: https://your-domain.com/telegram/webhook
```

This requires exposing an HTTPS endpoint.

## Security Notes

- Keep your bot token secret
- Consider enabling 2FA on your Telegram account
- Review who has access to chats where the bot is subscribed
- The bot can see all messages in groups where it's added
