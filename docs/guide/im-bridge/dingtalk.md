# DingTalk Setup

Connect your CCCC working group to DingTalk for enterprise collaboration.

## Overview

DingTalk (钉钉) is ideal for:

- Chinese enterprises
- Alibaba ecosystem users
- Teams already using DingTalk

## Prerequisites

- DingTalk enterprise account with admin access
- CCCC installed and running

## Step 1: Create an Application

1. Go to [DingTalk Open Platform](https://open.dingtalk.com/)
2. Log in with your enterprise admin account
3. Click **Application Development** → **Internal Development**
4. Click **Create Application**
5. Fill in:
   - Application Name (e.g., "CCCC Bot")
   - Application Description
   - Application Icon
6. Click **Confirm**

## Step 2: Configure Permissions

1. Go to **Permissions**
2. Apply for the following permissions:

| Permission | Purpose |
|------------|---------|
| `qyapi_robot_sendmsg` | Send robot messages in conversations |
| `Robot.SingleChat.ReadWrite` | Single chat robot management |
| `qyapi_chat_read` | Read group basic info |

3. Click to enable each permission (no approval needed for internal apps)

## Step 3: Configure CCCC

1. In your application, go to **Credentials & Basic Info**
2. Copy **AppKey** and **AppSecret**

::: warning Security
Keep your AppSecret confidential. Rotate it periodically.
:::

### Option A: Via Web UI

1. Open the CCCC Web UI at `http://127.0.0.1:8848/`
2. Go to **Settings** (gear icon in header)
3. Navigate to the **IM Bridge** tab
4. Select **DingTalk** as the platform
5. Enter your credentials:
   - **App Key**: Your DingTalk AppKey
   - **App Secret**: Your DingTalk AppSecret
6. Click **Save Config**

### Option B: Via CLI

First set environment variables:

```bash
export DINGTALK_APP_KEY="your_app_key"
export DINGTALK_APP_SECRET="your_app_secret"
```

Then configure CCCC:

```bash
cccc im set dingtalk \
  --app-key-env DINGTALK_APP_KEY \
  --app-secret-env DINGTALK_APP_SECRET
```

Both methods save to `group.yaml`:

```yaml
im:
  platform: dingtalk
  app_key_env: DINGTALK_APP_KEY
  app_secret_env: DINGTALK_APP_SECRET
```

## Step 4: Start the Bridge

### Via Web UI

Click the **Save Config** button in the IM Bridge settings. The bridge will start automatically and show **Running** status.

### Via CLI

```bash
cccc im start
```

Verify it's running:

```bash
cccc im status
```

## Step 5: Configure Stream Mode

::: warning Prerequisite
The CCCC IM Bridge must be running before you can configure message receiving mode.
:::

1. Go back to [DingTalk Open Platform](https://open.dingtalk.com/)
2. Navigate to your app → **Application Capabilities** → **Robot**
3. For **Message receiving mode**, select **Stream Mode** (recommended)

### Stream Mode (Recommended)

Stream Mode doesn't require a public URL:

1. Select **Stream Mode**
2. CCCC will automatically establish a WebSocket connection

### HTTP Mode

If using HTTP mode instead:

1. Select **HTTP Mode**
2. Configure:
   - Message receiving URL
   - Token (for verification)
   - AES Key (for encryption)

## Step 6: Enable Robot

::: tip Why This Step?
You must enable the Robot capability so users can find and chat with your bot after the app is published.
:::

1. In **Application Capabilities** → **Robot**
2. Enable the robot capability
3. Configure robot settings:
   - Robot name
   - Robot avatar

## Step 7: Publish the Application

1. Go to **Version Management**
2. Create a new version
3. Configure visibility:
   - All employees
   - Specific departments
   - Specific users
4. Publish the version

## Step 8: Subscribe in DingTalk

1. Find the robot in your DingTalk application
2. Add it to a group chat or start a direct conversation
3. Send `/subscribe`
4. Confirm the subscription

## Usage

### Sending Messages to Agents

In group chats, @mention the bot first, then use the `/send` command:

```
@YourBotName /send Please check the code quality
```

In direct messages with the bot, you can use `/send` directly:

```
/send Please check the code quality
```

::: warning Important
- In group chats, you must @mention the bot before using commands
- Plain messages without the `/send` command are ignored
:::

### Targeting Specific Agents

Use `@mention` syntax with the `/send` command:

```
/send @foreman Please assign today's development tasks
/send @reviewer Please review the latest commits
/send @all Status update please
```

### Receiving Messages

After subscribing, you will automatically receive:
- Agent responses
- Status updates
- Error notifications

Use `/verbose` to toggle whether you see agent-to-agent messages.

### Message Types

DingTalk supports various message types:

- **Text**: Plain text messages
- **Markdown**: Formatted text
- **Link**: URL cards
- **ActionCard**: Interactive cards with buttons

CCCC automatically selects the appropriate format.

### File Sharing

Attach files to your message. DingTalk files are downloaded and stored in CCCC's blob storage, then forwarded to agents.

## Commands Reference

| Command | Description |
|---------|-------------|
| `/subscribe` | Start receiving messages from CCCC |
| `/unsubscribe` | Stop receiving messages |
| `/send <message>` | Send a message to agents |
| `/send @<actor> <message>` | Send to a specific agent |
| `/status` | Show group and agent status |
| `/pause` | Pause message delivery |
| `/resume` | Resume message delivery |
| `/verbose` | Toggle verbose mode (see all agent messages) |
| `/help` | Show available commands |

## Troubleshooting

### "Invalid appkey" Error

1. Verify AppKey in DingTalk Open Platform
2. Check environment variable is set correctly
3. Ensure the application is published

### "No permission" Error

1. Check required permissions are granted
2. Verify the app is visible to the user
3. Ensure the app version is published

### Robot not responding

1. Check if the robot is added to the chat
2. Verify the bridge is running:
   ```bash
   cccc im status
   ```
3. Check logs:
   ```bash
   cccc im logs -f
   ```
4. Make sure CCCC IM Bridge is running

### Connection issues (Stream Mode)

If using Stream Mode and connection drops:

1. Check network connectivity
2. Restart the bridge:
   ```bash
   cccc im stop
   cccc im start
   ```

### Webhook verification failed (HTTP Mode)

1. Verify the Token and AES Key are correct
2. Ensure your webhook URL is accessible
3. Check the signature verification logic

## Advanced Configuration

### Stream Mode Settings

```yaml
im:
  platform: dingtalk
  app_key_env: DINGTALK_APP_KEY
  app_secret_env: DINGTALK_APP_SECRET
  mode: stream  # or 'http'
```

### HTTP Mode with Verification

```yaml
im:
  platform: dingtalk
  app_key_env: DINGTALK_APP_KEY
  app_secret_env: DINGTALK_APP_SECRET
  mode: http
  webhook_token_env: DINGTALK_WEBHOOK_TOKEN
  webhook_aes_key_env: DINGTALK_WEBHOOK_AES_KEY
```

### Custom Robot (Webhook Only)

For simpler outbound-only messaging, use a Custom Robot:

1. In a group chat, click **Group Settings** → **Intelligent Group Assistant**
2. Add a **Custom Robot**
3. Copy the Webhook URL
4. Configure in CCCC:

```yaml
im:
  platform: dingtalk
  mode: webhook
  webhook_url_env: DINGTALK_WEBHOOK_URL
  webhook_secret_env: DINGTALK_WEBHOOK_SECRET  # Optional
```

Note: Custom robots can only send messages, not receive.

### Message Templates

Use predefined templates for consistent formatting:

```yaml
im:
  platform: dingtalk
  app_key_env: DINGTALK_APP_KEY
  app_secret_env: DINGTALK_APP_SECRET
  templates:
    status: actioncard  # Use ActionCard for status messages
    error: markdown     # Use Markdown for errors
```

## Security Notes

- Rotate AppSecret periodically
- Use IP whitelist for webhook endpoints
- Enable message encryption for sensitive data
- Review robot access in admin console
- Consider using outpost mode for additional security
- Audit message logs regularly
- Limit robot visibility to necessary employees
